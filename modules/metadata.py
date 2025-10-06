'''*********************************************************************************************************************************
Tool Name: metadata.py
Version: Python 3.11.10
Author: DAS (OWRB GIS)
Description: 
            Module to handle metadata imports/exports with enhanced functionality.

History: 
            Initial coding - DAS 20250617
            Enhanced with batch operations and validation - DAS 20250813
            
Usage: Use in main script
Comments: Added batch operations, validation, and modern XML handling
************************************************************************************************************************************'''

import arcpy
import os
import logging
import shutil
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

class MetadataError(Exception):
    """Custom exception for metadata operations."""
    pass

class MetadataManager:
    def __init__(self, metadata_dir: str): 
        self.metadata_dir = Path(metadata_dir)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        # Common namespaces for ISO 19139
        self.namespaces = {
            'gmd': 'http://www.isotc211.org/2005/gmd',
            'gco': 'http://www.isotc211.org/2005/gco',
            'gml': 'http://www.opengis.net/gml/3.2',
            'gts': 'http://www.isotc211.org/2005/gts'
        }

    def import_metadata_safe(self, target_fc: str, xml_file: str) -> bool:
        '''Import metadata from XML to a feature class with validation.'''

        try:
            xml_path = self.metadata_dir / xml_file

            if not xml_path.exists():
                raise MetadataError(f"Metadata file does not exist: {xml_path}")
            
            if not arcpy.Exists(target_fc):
                raise MetadataError(f"Target feature class does not exist: {target_fc}")
            
            # Validate XML before import
            if not self._validate_xml_metadata(xml_path):
                raise MetadataError(f"Invalid XML metadata: {xml_path}")
            
            # Import metadata
            importer = arcpy.MetadataImporter(str(xml_path))
            importer.importMetadata(target_fc)

            logger.info(f"Successfully imported metadata from {xml_file} to {target_fc}")
            return True
        
        except Exception as e:
            logger.error(f"Metadata import failed for {target_fc}: {e}")
            return False
    
    def batch_import_metadata(self, metadata_mappings: Dict[str, str]) -> bool:
        """Import metadata for multiple feature classes in parallel."""

        logger.info(f"Starting batch metadata import for {len(metadata_mappings)} items")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(self.import_metadata_safe, target_fc, xml_file)
                for target_fc, xml_file in metadata_mappings.items()
            ]

            results = [future.result() for future in as_completed(futures)]

        success_count = sum(results)
        logger.info(f"Batch metadata import completed: {success_count}/{len(results)} successful")
        return all(results)
    
    def update_metadata_dates(self, xml_files: List[str], update_date: Optional[datetime] = None) -> bool:
        """Update publication dates in multiple metadata files."""
        
        if update_date is None:
            update_date = datetime.now()

        logger.info(f"Updating metadata dates to {update_date.strftime('%Y-%m-%d')}")

        success_count = 0
        for xml_file in xml_files:
            if self._update_single_metadata_date(xml_file, update_date):
                success_count += 1

        logger.info(f"Updated dates in {success_count}/{len(xml_files)} metadata files")
        return success_count == len(xml_files)
    
    def _update_single_metadata_date(self, xml_file: str, update_date: datetime) -> bool:
        """Update publication date in a single metadata file."""

        try:
            xml_path = self.metadata_dir / xml_file

            if not xml_path.exists():
                logger.warning(f"Metadata file does not exist: {xml_path}")
                return False
            
            # Parse XML with namespace awareness
            tree = ET.parse(str(xml_path))
            root = tree.getroot()

            # Update various date fields that might exist
            date_paths = [
                ".//gmd:dateStamp/gco:Date",
                ".//gmd:dateStamp/gco:DateTime",
                ".//gmd:date/gmd:CI_Date/gmd:date/gco:Date",
                ".//gmd:publicationDate/gco:Date"
            ]

            updated = False
            for path in date_paths:
                elements = root.findall(path, self.namespaces)
                for elem in elements:
                    elem.text = update_date.strftime("%Y-%m-%d")
                    updated = True

            if updated:
                # Backup original file
                backup_path = xml_path.with_suffix('.xml.bak')
                shutil.copy2(xml_path, backup_path)

                # Write updated XML
                tree.write(str(xml_path), encoding="utf-8", xml_declaration=True)
                logger.info(f"Updated metadata date in {xml_file}")
                return True
            else:
                logger.warning(f"No date elements found to update in {xml_file}")
                return False
            
        except Exception as e:
            logger.error(f"Failed to update metadata data in {xml_file}: {e}")
            return False
        
    def _validate_xml_metadata(self, xml_path: Path) -> bool:
        """Validate XML metadata file structure."""

        try:
            tree = ET.parse(str(xml_path))
            root = tree.getroot()

            # Check for ISO 19139 structure
            if root.tag.endswith('MD_Metadata'):
                return True
            
            # Check for FGDC structure
            if root.tag == 'metadata':
                return True
            
            logger.warning(f"Unknown metadata format in {xml_path}")
            return False
        
        except ET.ParseError as e:
            logger.error(f"XML parsing error in {xml_path}: {e}")
            return False
        
        except Exception as e:
            logger.error(f"Metadata validation error for {xml_path}: {e}")
            return False
        
    def export_metadata_to_html(self, source_fc: str, output_html: str) -> bool:
        """Export metadata to HTML format (iExportSHPMetadata)."""

        try:
            # Use ArcGIS metadata tools
            temp_xml = self.metadata_dir / "temp_export.xml"

            # Export metadata to XML first
            arcpy.conversion.ExportMetadata(
                source_fc, str(temp_xml), "ISO19139"
            )

            # Convert XML to HTML using ArcGIS stylesheets
            arcpy.conversion.XSLTransform(
                str(temp_xml),
                r"C:\Program Files\ArcGIS\Pro\Resources\Metadata\Stylesheets\ISO19139_to_HTML.xsl",
                output_html
            )

            # Cleanup temp file
            if temp_xml.exists():
                temp_xml.unlink()

            logger.info(f"Exported metadata to HTML: {output_html}")
            return True
        
        except Exception as e:
            logger.error(f"HTML metadata export failed: {e}")
            return False
        
    def update_fgdc_metadata_modern(self, xml_file: str, output_txt: str) -> bool:
        """Modern FGDC metadata handling without external MP tool."""

        try:
            xml_path = self.metadata_dir / xml_file

            if not xml_path.exists():
                raise MetadataError(f"FGDC XML file does not exist: {xml_path}")
            
            # Parse FGDC XML and convert to text
            tree = ET.parse(str(xml_path))
            root = tree.getroot()

            # Extract key FGDC elements
            metadata_text = self.fgdc_to_text(root)

            # Write to text file
            with open(output_txt, 'W', encoding='utf-8') as f:
                f.write(metadata_text)

            logger.info(f"Converted FGDC metadata to text: {output_txt}")
            return True
        
        except Exception as e:
            logger.error(f"FGDC metadata conversion failed: {e}")
            return False
        
    def _fgdc_to_text(self, root: ET.Element) -> str:
        """Convert FGDC XML elements to readable text format."""

        lines = []

        # Title
        title = root.find('.//title')
        if title is not None:
            lines.append(f"Title: {title.text}")

        # Abstract
        abstract = root.find('.//abstract')
        if abstract is not None:
            lines.append(f"Abstract: {abstract.text}")

        # Publication Date
        pubdate = root.find('.//pubdate')
        if pubdate is not None:
            lines.append(f"Publication Date: {pubdate.text}")

        # Contact Information
        contact = root.find('.//cntinfo')
        if contact is not None:
            org = contact.find('.//cntorg')
            if org is not None:
                lines.append(f"Contact Organization: {org.text}")

        return '\n'.join(lines)
    
    def backup_metadata_files(self, backup_dir: Optional[str] = None) -> bool:
        """Create backup of all metadata files."""

        try:
            if backup_dir is None:
                backup_dir = self.metadata_dir / "backups" / datetime.now().strftime("%Y%m%d_%H%M%S")

            backup_path = Path(backup_dir)
            backup_path.mkdir(parents=True, exist_ok=True)

            xml_files = list(self.metadata_dir.glob("*.xml"))

            for xml_file in xml_files:
                shutil.copy2(xml_file, backup_path / xml_file.name)

            logger.info(f"Backed up {len(xml_files)} metadata files to {backup_path}")
            return True
        
        except Exception as e:
            logger.error(f"Metadata backup failed: {e}")
            return False
        
    def create_metadata_report(self) -> Dict[str, Any]:
        """Generate a report of all metadata files and their status."""

        report = {
            "metadata_directory": str(self.metadata_dir),
            "scan_time": datetime.now().isoformat(),
            "files": []
        }

        for xml_file in self.metadata_dir.glob("*.xml"):
            file_info = {
                "filename": xml_file.name,
                "size_kb": round(xml_file.stat().st_size / 1024, 2),
                "modified": datetime.fromtimestamp(xml_file.stat().st_mtime).isoformat(),
                "valid": self._validate_xml_metadata(xml_file)
            }

            # Try to extract title and date
            try:
                tree = ET.parse(str(xml_file))
                root = tree.getroot()

                # Look for title
                title_elem = root.find('.//title') or root.find('.//gmd:title/gco:CharacterString', self.namespaces)
                if title_elem is not None:
                    file_info["title"] = title_elem.text

                # Look for date
                date_elem = root.find('.//pubdate') or root.find('.//gmd:dateStamp/gco:Date', self.namespaces)
                if date_elem is not None:
                    file_info["publication_date"] = date_elem.text

            except Exception as e:
                file_info["parse_error"] = str(e)

            report["files"].append(file_info)

        return report