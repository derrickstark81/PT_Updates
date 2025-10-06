'''*********************************************************************************************************************************
Tool Name: database.py
Version: Python 3.11.10
Author: DAS (OWRB GIS)
Description: 
            Module to centralize all database operations with modern patterns.

History: 
            Initial coding - DAS 20250617
            Enhanced with async, better error handling - DAS 20250811
            
Usage: Use in main script
Comments: Added context managers, retry logic, and performance optimizations
************************************************************************************************************************************'''
import arcpy, logging, time, pyodbc
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
arcpy.env.overwriteOutput = True

class DatabaseError(Exception):
    """Custom exception for database operations."""
    pass

class SDEDatabase:
    def __init__(self, connections: dict):
        self.test_SDE = connections["test_SDE"]
        self.prod_SDE = connections["prod_SDE"]
        self.oracle_ODC = connections["oracle_ODC"]
        self.csa_Prod_SDE = connections["csa_Prod_SDE"]

        # Set ArcPy environment
        arcpy.env.overwriteOutput = True
        arcpy.env.workspace = self.test_SDE

    @contextmanager
    def sde_connection(self, connection_path: str):
        """Context manager for SDE connections."""
        
        original_workspace = connection_path

        try:
            arcpy.env.workspace = connection_path
            yield connection_path
        except Exception as e:
            logger.error(f"SDE connection error: {e}")
            raise DatabaseError(f"Failed to connect to {connection_path}: {e}")
        finally:
            arcpy.env.workspace = original_workspace

    def retry_operation(self, func, max_retries: int = 3, delay: float = 1.0):
        """Retry database operations with exponential backoff."""
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if attempt == max_retries - 1:
                    raise DatabaseError(f"Operation failed after {max_retries} attempts: {e}")
                logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2 # Exponential backoff

    def export_table_safe(self, src, tgt, where=None, max_attempts=3):
        """Export table with retry logic and validation."""
        last_err = None
        for attempt in range(1, max_attempts+1):
            try:
                if arcpy.Exists(tgt):
                    arcpy.management.Delete(tgt)
                arcpy.conversion.ExportTable(src, tgt, where_clause=where or "")
                return {"source": src, "target": tgt, "rows": int(arcpy.management.GetCount(tgt)[0])}
            except Exception as e:
                last_err = e
                self.logger.warning(f"Attempt {attempt} failed: {e}. Retrying in {attempt}.0s...")
                time.sleep(attempt) # 1s, 2s, 3s
        raise RuntimeError(f"Operation failed after {max_attempts} attempts: {last_err}")
    
    def truncate_table_safe(self, table_path: str) -> bool:
        """Safely truncate table with validation."""
        def _truncate():
            if not arcpy.Exists(table_path):
                logger.warning(f"Table does not exist for truncation: {table_path}")
                return False
            
            before_count = int(arcpy.management.GetCount(table_path)[0])
            arcpy.management.TruncateTable(table_path)
            after_count = int(arcpy.management.GetCount(table_path)[0])

            logger.info(f"Truncated {table_path}: {before_count} -> {after_count} records")
            return True
        
        return self.retry_operation(_truncate)
    
    def truncate_and_copy(self, src: str, tgt: str):
        '''Idempotent copy to CSA/Prod: create target if missing, else truncate and append.'''
        if not arcpy.Exists(src):
            raise FileNotFoundError(f"Source does not exist: {src}")
        
        if arcpy.Exists(tgt):
            arcpy.management.TruncateTable(tgt)
            arcpy.management.Append(inputs=src, target=tgt, schema_type="NO_TEST")
        else:
            arcpy.management.CopyRows(src, tgt) # Creates table with schema on first run
    
    def append_data_safe(self, source: str, target: str, schema_type: str = "TEST") -> bool:
        """Safely append data with validation."""

        def _append():
            if not arcpy.Exists(source):
                raise DatabaseError(f"Source does not exist: {source}")
            if not arcpy.Exists(target):
                raise DatabaseError(f"Target does not exist: {target}")
            
            before_count = int(arcpy.management.GetCount(target)[0])
            source_count = int(arcpy.management.GetCount(source)[0])

            arcpy.management.Append(source, target, schema_type)

            after_count = int(arcpy.management.GetCount(target)[0])
            expected_count = before_count + source_count

            if after_count != expected_count:
                logger.warning(f"Append count mismatch. Expected: {expected_count}, Actual: {after_count}")

            logger.info(f"Appended {source_count} records to {target}")
            return True
        
        return self.retry_operation(_append)
    
    # Phase 1 is to run the bunny tool
    
    def execute_phase_2_test_updates(self) -> bool:
        """Execute Phase 2: Update PT Points and Lands on Test SDE"""

        logger.info("=== Starting Phase 2: Test SDE Updates ===")

        with ThreadPoolExecutor(max_workers=3) as executor:

            # Submit all initial export tasks
            export_features = [
                executor.submit(self._export_initial_data),
                executor.submit(self.update_pt_points_modern),
                executor.submit(self.update_pt_lands_modern)
            ]

            # Wait for completion and check results
            results = [future.result() for future in as_completed(export_features)]

        if not all(results):
            logger.error("Phase 2 failed - some operations unsuccessful")
            return False
        
        # Create PT Lands Table (depends on lands update)
        return self._create_pt_lands_table()
    
    def _export_initial_data(self) -> bool:
        """Export initial data for PT updates."""

        try:
            # Concurrent initial exports
            operations = [
                (f"{self.test_SDE}\\OWRBGIS.WR_PT_Points", f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_TMP_Legal", None),
                (f"{self.oracle_ODC}\\WR.WR_STPERMIT", f"{self.test_SDE}\\OWRBGIS.D_WR_STPERMIT", None),
                (f"{self.prod_SDE}\\WR.WR_STLEGAL", f"{self.test_SDE}\\OWRBGIS.D_WR_STLEGAL", None)
            ]

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(self.export_table_safe, src, tgt, where)
                           for src, tgt, where in operations]
                results = [future.result() for future in as_completed(futures)]

            return all(results)
        
        except Exception as e:
            logger.error(f"Initial data export failed: {e}")
            return False
        
    def update_pt_points_modern(self) -> bool:
        """Modern implementation of 2aUpdatePTPointsOnTest with performance optimizations."""

        try:
            logger.info("Updating PT Points with modern approach")

            # Step 1: Create temp table and join operations
            temp_legal = f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_TMP_Legal"

            # Step 2: Use pandas-like operations for joins (via arcpy.da)
            self._perform_lookup_joins(temp_legal, "WATER_CODE")
            self._perform_lookup_joins(temp_legal, "PURPOSE_CODE")
            self._perform_lookup_joins(temp_legal, "SIC_CODE")

            # Step 3: Create All and Active layers
            self._create_points_all_layer(temp_legal)
            self._create_points_active_layer()

            return True

        except Exception as e:
            logger.error(f"PT Points update failed: {e}")
            return False

    def _perform_lookup_joins(self, target_fc: str, lookup_field: str) -> None:
        """Perform lookup value joins using modern arcpy.da cursors."""  

        # Create lookup dictionary for faster joins
        lookup_dict = {}
        with arcpy.da.SearchCursor(f"{self.prod_SDE}\\OWRBGIS.WR_LOOKUP_VALUES",
                                   ["CODE_VALUE", "DESCRIPTION"]) as cursor:
            for row in cursor:
                lookup_dict[row[0]] = row[1]

        # Update target features using the lookup
        with arcpy.da.UpdateCursor(target_fc, [lookup_field]) as cursor:
            for row in cursor:
                if row[0] in lookup_dict:
                    row[0] = lookup_dict[row[0]]
                    cursor.updateRow(row)

    def _create_points_all_layer(self, source_fc: str) -> None:
        """Create and populate WR_PT_Points_All layer."""

        all_layer = f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_All"

        # Export with filter
        arcpy.conversion.ExportFeatures(
            source_fc, all_layer,
            where_clause="STATUS_CODE IS NULL OR STATUS_CODE IN ('A', 'E')"
        )

    def _create_points_active_layer(self) -> None:
        """Create active points layer based on expiration date."""

        # Dynamic date calculation
        current_date = datetime.now().strftime("%Y-%m-%d")

        arcpy.management.MakeFeatureLayer(
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_All",
            "WR_PT_Points_All_Layer",
            f"EXP_DATE >= date '{current_date}'"
        )

        arcpy.conversion.ExportFeatures(
            "WR_PT_Points_All_Layer",
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_Active"
        )

    def update_pt_lands_modern(self) -> bool:
        """Modern implementation of 2bUpdatePTLandsOnTest."""

        src_table_path = f"{self.oracle_ODC}\\WR.WR_STPERMIT"

        self.logger.info(f"PT Lands: SOURCE = {src_table_path}")
        try:
            fields = [f.name for f in arcpy.ListFields(src_table_path)]
            self.logger.info(f"PT Lands: FIELDS = {src_table_path}")
        except Exception as e:
            self.logger.error(f"PT Lands: failed to ListFields on {src_table_path}: {e}")

        try:
            logger.info("Updating PT Points with modern approach")

            # Step 1: Create temp table and join operations
            temp_legal = f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_TMP_Legal"

            # Step 2: Use pandas-like operations for joins (via arcpy.da)
            self._perform_lookup_joins(temp_legal, 'WATER_CODE')
            self._perform_lookup_joins(temp_legal, 'PURPOSE_CODE')
            self._perform_lookup_joins(temp_legal, 'SIC_CODE')

            # Step 3: Create All and Active layers
            self._create_points_all_layer(temp_legal)
            self._create_points_active_layer()

            return True

        except Exception as e:
            logger.error(f"PT Points update failed: {e}")
            return False

    def _perform_lookup_joins(self, target_fc: str, lookup_field: str) -> None:
        """Perform lookup value joins using modern arcpy.da cursors."""     

        # Create lookup dirctionary for faster joins
        lookup_dict = {}
        
        with arcpy.da.SearchCursor(f"{self.prod_SDE}\\OWRBGIS.WR_LOOKUP_VALUES",
                                   ["CODE_VALUE", "DESCRIPTION"]) as cursor:
            
            for row in cursor:
                lookup_dict[row[0]] = row[1]

        # Update target features using the lookup
        with arcpy.da.UpdateCursor(target_fc, [lookup_field]) as cursor:
            for row in cursor:
                if row[0] in lookup_dict:
                    row[0] = lookup_dict[row[0]]
                    cursor.updateRow(row)

    def _create_points_all_layers(self, source_fc: str) -> None:
        """Create and populate WR_PT_Points_All layer."""

        all_layer = f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_All"

        # Export with filter
        arcpy.conversion.ExportFeatures(
            source_fc, all_layer,
            where_clause="STATUS_CODE IS NULL OR STATUS_CODE IN ('A', 'E')"
        )

    def _create_points_active_layer(self) -> None:
        """Create active points layer based on expiration date."""

        # Dynamic date calculation
        current_date = datetime.now().strftime("%Y-%m-%d")

        arcpy.management.MakeFeatureLayer(
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_All",
            "WR_PT_Points_All_Layer",
            f"EXP_DATE >= date'{current_date}'"
        )

        arcpy.conversion.ExportFeatures(
            "WR_PT_Points_All_Layer",
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_Active"
        )

    def update_pt_lands_modern(self) -> bool:
        """Modern implementation of 2bUpdatePTLandsOnTest."""

        try:
            logger.info("Updating PT Lands with modern approach")

            # Similar pattern to points but for lands
            lands_temp = f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_TMP_Legal"

            # Export base lands
            self.export_table_safe(
                f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands",
                lands_temp
            )

            # Perform joins
            self._perform_lookup_joins(lands_temp, "WATER_CODE")
            self._perform_lookup_joins(lands_temp, "PURPOSE_CODE")
            self._perform_lookup_joins(lands_temp, "SIC_CODE")

            # Create All and Active layers
            self._create_lands_layers(lands_temp)

            return True
        
        except Exception as e:
            logger.error(f"PT Lands update failed: {e}")
            return False
        
    def _create_lands_layers(self, source_fc: str) -> None:
        """Create lands All and Active layers."""

        # Create All layer
        lands_all = f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_All"
        arcpy.conversion.ExportFeatures(
            source_fc, lands_all,
            where_clause="STATUS_CODE IS NULL OR STATUS_CODE IN ('A', 'E')"
        )

        # Create Active layer
        current_date = datetime.now().strftime("%Y-%m-%d")
        arcpy.management.MakeFeatureLayer(
            lands_all, "WR_PT_Lands_All_Layer", 
            f"EXP_DATE >= date'{current_date}'"
        )

        arcpy.conversion.ExportFeatures(
            "WR_PT_Lands_All_Layer",
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Active"
        )

    def _create_pt_lands_table(self) -> bool:
        """Create PT Lands Table (2cCreatePTLandsTable)."""

        try:
            lands_table = f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Table"

            # Truncate and repopulate
            self.truncate_table_safe(lands_table)

            # Create table view and append
            arcpy.management.MakeTableView(
                f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Active",
                "WR_PT_Lands_Active_View"
            )

            return self.append_data_safe("WR_PT_Lands_Active_View", lands_table)
        
        except Exception as e:
            logger.error(f"PT Lands Table creation failed: {e}")
            return False

    def execute_phase_3_production_sync(self) -> bool:
        """Execute Phase 3: Sync to Production and CSA."""   

        logger.info("===Starting Phase 3: Prodcution Sync ===")

        sync_operations = [

            # CSA SDE Production Operations
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Table", f"{self.csa_Prod_SDE}\\owrp.sde.WR_PT_Lands_Table"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_All", f"{self.csa_Prod_SDE}\\owrp.sde.WR_PT_Lands_All"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Active", f"{self.csa_Prod_SDE}\\owrp.sde.WR_PT_Lands"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_All", f"{self.csa_Prod_SDE}\\owrp.sde.WR_PT_Points_All"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_Active", f"{self.csa_Prod_SDE}\\owrp.sde.WR_PT_Points"),

            # Oracle SDE Prodcution Operations
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Table", f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands_Table"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_All", f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands_All"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands_Active", f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_All", f"{self.prod_SDE}\\OWRBGIS.WR_PT_Points_All"),
            (f"{self.test_SDE}\\OWRBGIS.WR_PT_Points_Active", f"{self.prod_SDE}\\OWRBGIS.WR_PT_Points")

        ]

        missing = [src for src, _ in sync_operations if not arcpy.Exists(src)]
        if missing:
            for m in missing:
                logger.error(f"Source does not exist: {m}")
            return False
        
        ok = True

        # Excecute sync operations in parallel batches
        batch_size = 3

        for i in range(0, len(sync_operations), batch_size):
            batch = sync_operations[i:i + batch_size]
            logger.info(f"Running sync batch {i//batch_size + 1}: {len(batch)} opes")

            with ThreadPoolExecutor(max_workers=batch_size) as executor:
                futures = [executor.submit(self.truncate_and_copy, src, tgt) for src, tgt in batch]

                for future in as_completed(futures):
                    try:
                        future.result() # raises if failed
                    except Exception as e:
                        ok = False
                        logger.error(f"Batch {i//batch_size + 1} op failed: {e}")
                
                batch_results = [future.result() for future in as_completed(futures)]

                if not all(batch_results):
                    logger.error(f"Batch {i//batch_size + 1} sync failed")
                    return False
                
        logger.info("Phase 3 production sync completed")
        return True
    
    def _sync_single_layer(self, source: str, target: str) -> bool:
        """Sync a single layer from test to production."""

        try:
            # Truncate target
            if not self.truncate_table_safe(target):
                return False
            
            # Append data
            return self.append_data_safe(source, target, "NO_TEST")
        
        except Exception as e:
            logger.error(f"Failed to sync {source} -> {target}: {e}")
            return False
        
    def execute_phase_4_gdb_operations(self, temp_gdb_path: str) -> bool:
        """Execute Phase 4: File Geodatabase Operations."""

        logger.info("=== Starting Phase 4: Geodatabase Operations ===")

        # Delete existing temp GDB
        if Path(temp_gdb_path).exists():
            arcpy.management.Delete(temp_gdb_path)
            logger.info(f"Deleted existing temp GDB: {temp_gdb_path}")

        # Create new temp GDB
        gdb_folder = str(Path(temp_gdb_path).parent)
        gdb_name = Path(temp_gdb_path).stem

        arcpy.management.CreateFileGDB(gdb_folder, f"{gdb_name}.gdb", "CURRENT")
        logger.info(f"Create temp GDB: {temp_gdb_path}")

        # Export production data to temp GDB
        export_operations = [
            (f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands_Table", f"{temp_gdb_path}\\WR_PT_Lands_Table"),
            (f"{self.test_SDE}\\OWRBGIS.D_WR_STPERMIT", f"{temp_gdb_path}\\WR_STPERMIT",
             "ENTITY_NAME IS NOT NULL AND ENTITY_NAME NOT IN ('The Muppets', 'Pat''s Oil and Gas')"),
            (f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands_All", f"{temp_gdb_path}\\WR_PT_Lands_All"),
            (f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands", f"{temp_gdb_path}\\WR_PT_Lands"),
            (f"{self.prod_SDE}\\OWRBGIS.WR_PT_Points_All", f"{temp_gdb_path}\\WR_PT_Points_All"),
            (f"{self.prod_SDE}\\OWRBGIS.WR_PT_Points", f"{temp_gdb_path}\\WR_PT_Points")
             
        ]

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = []
            for op in export_operations:
                if len(op) == 2:
                    src, tgt = op; where = None
                else:
                    src, tgt, where = op
                futures.append(executor.submit(self.export_table_safe, src, tgt, where))
        results = [f.result() for f in futures]
        return results
    
    def create_relationship_classes(self, temp_gdb_path: str) -> bool:
        """Create relationship classes in temp GDB."""

        try:
            # Add YEAR_ISSUED field to WR_STPERMIT
            stpermit_table = f"{temp_gdb_path}\\WR_STPERMIT"
            arcpy.management.AddField(stpermit_table, "YEAR_ISSUED", "SHORT")

            # Calculate YEAR_ISSUED using modern Python expression
            arcpy.management.CalculateField(
                stpermit_table, "YEAR_ISSUED",
                "datetime.datetime.strptime(!DATE_ISSUED!, '%Y-%m-%d %H:%M:%S').year",
                "PYTHON3"
            )

            # Create relationship class
            relationships = [
                (f"{temp_gdb_path}\\WR_STPERMIT", f"{temp_gdb_path}\\WR_PT_Lands_All",
                 f"{temp_gdb_path}\\ZRC_WR_STPERMIT_WR_PT_Lands_All"),
                (f"{temp_gdb_path}\\WR_STPERMIT", f"{temp_gdb_path}\\WR_PT_Points_All",
                 f"{temp_gdb_path}\\ZRC_WR_STPERMIT_WR_PT_Points_All")
            ]

            for origin, destination, rel_class in relationships:
                arcpy.management.CreateRelationshipClass(
                    origin, destination, rel_class, "SIMPLE",
                    Path(destination).stem, Path(origin).stem, "NONE",
                    "ONE_TO_MANY", "", "PERMIT_NUMBER", "PERMIT_NUMBER"
                )

                logger.info(f"Created relationship class: {rel_class}")

            return True
        
        except Exception as e:
            logger.error(f"Relationship class creation failed: {e}")
            return False
        
    def create_identical_points_table(self, temp_gdb_path: str, years_back: int = 2) -> bool:
        """Create table for identifying duplicate PT locations (c2CreateIdenticalPointsTable)."""

        try:
            # Calculate dynamic date filter
            cutoff_date = (datetime.now() - timedelta(days=years_back*365)).strftime("%Y-%m-%d")

            # Create feature layer with date filter
            points_all = f"{temp_gdb_path}\\WR_PT_Points_All"
            arcpy.management.MakeFeatureLayer(
                points_all, "WR_PT_Points_All_Layer",
                f"DATE_ISSUED >= date'{cutoff_date} 00:00:00'"
            )

            # Find identical points by geometry
            identical_table = f"{temp_gdb_path}\\WR_PT_Points_Identical"
            arcpy.management.FindIdentical(
                "WR_PT_Points_All_Layer", identical_table,
                "SHAPE", "", "", "ONLY_DUPLICATES"
            )

            # Create relationship class for identical points
            rel_class = f"{temp_gdb_path}\\ZRC_WR_PT_Points_All_WR_PT_Points_Identical"
            arcpy.management.CreateRelationshipClass(
                identical_table, points_all, rel_class, "SIMPLE",
                "WR_PT_Points_Identical", "WR_PT_Points_All", "NONE", "ONE_TO_MANY", "", "OBJECTID", "IN_FID"
            )

            logger.info("Identical points table created successfully")
            return True
        
        except Exception as e:
            logger.error(f"Identical points table creation failed: {e}")
            return False
        
    def export_public_data(self, temp_gdb_path: str, output_dir: str) -> bool:
        """Export data for public download (d1ExportShapefileAndTable)"""

        try:
            logger.info("Exporting public data files")

            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            # Export shapefile
            points_shp = output_path / "WR_PT_Wells_and_Diversions_Points.shp"
            arcpy.conversion.ExportFeatures(
                f"{temp_gdb_path}\\WR_PT_Points",
                str(points_shp)
            )

            # Export table as dBASE
            lands_dbf = output_path / "WR_PT_Lands_Table.dbf"
            arcpy.conversion.TableToDBASE(
                f"{temp_gdb_path}\\WR_PT_Lands_Table",
                str(output_path)
            )

            logger.info(f"Public data exported to {output_dir}")
            return True
        
        except Exception as e:
            logger.error(f"Public data export failed: {e}")
            return False
    
    def create_summary_tables(self, temp_gdb_path: str, summary_dir: str) -> bool:
        """Create annual sumary tables (d2CreateSummaryTables)."""

        try:
            logger.info("Creating summary tables")

            # Get unique years from data
            years = self._get_unique_years(f"{temp_gdb_path}\\WR_STPERMIT")

            summary_path = Path(summary_dir)
            summary_path.mkdir(parents=True, exist_ok=True)

            # Create summary for each year
            for year in years:
                summary_table = summary_path / f"WR_sum_PT_{year}.dbf"

                # Use Summary Statistics tool
                arcpy.analysis.Statistics(
                    f"{temp_gdb_path}\\WR_STPERMIT",
                    str(summary_table),
                    [["PERMIT_NUMBER", "COUNT"], ["TOTAL_ACRE_FEET", "SUM"]],
                    ["YEAR_ISSUED", "PURPOSE", "COUNTY"],
                    where_clause=f"YEAR_ISSUED = {year}"
                )

                logger.info(f"Created summary table for {year}")

            return True
        
        except Exception as e:
            logger.error(f"Summary table creation failed: {e}")
            return False
    
    def _get_unique_years(self, table: str) -> List[int]:
        """Get unique years from YEAR_ISSUED field."""

        years = set()
        with arcpy.da.SearchCursor(table, ["YEAR_ISSUED"]) as cursor:
            for row in cursor:
                if row[0] is not None:
                    years.add(row[0])
        return sorted(list(years))
    
    def copy_gdb_to_arapaho(self, source_gdb: str, target_path: str) -> bool:
        """Copy GDB to Arapaho location (fCopyPTGDBtoArapaho)."""

        try:
            logger.info(f"Copying {source_gdb} to {target_path}")

            # Delete existing target GDB
            if Path(target_path).exists():
                arcpy.management.Delete(target_path)

            # Copy GDB
            arcpy.management.Copy(source_gdb, target_path)

            logger.info("GDB copy completed successfully")
            return True
        
        except Exception as e:
            logger.error(f"GDB copy failed: {e}")
            return False
        
    def cleanup_summary_tables(self, summary_dir: str) -> bool:
        """Delete old summary tables (aDeletePTGDB logic for tables)."""

        try:
            logger.info("Cleaning up old summary tables")

            summary_path = Path(summary_dir)
            if not summary_path.exists():
                logger.info("No summary directory to clean")
                return True
            
            # Delete all WR_sum_PT* files
            deleted_count = 0
            for file_path in summary_path.glob("WR_sum_PT*"):
                try:
                    file_path.unlink()
                    deleted_count += 1
                    logger.debug(f"Deleted: {file_path}")
                except Exception as e:
                    logger.warning(f"Could not delete {file_path}: {e}")

            logger.info(f"Cleaned up {deleted_count} summary table files")
            return True
        
        except Exception as e:
            logger.error(f"Summary table cleanup failed: {e}")
            return False
        
    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection status and information."""

        connections = {
            "test_SDE": self.test_SDE,
            "prod_SDE": self.prod_SDE,
            "oracle_ODC": self.oracle_ODC,
            "csa_Prod_SDE": self.csa_Prod_SDE
        }

        status = {}
        for name, path in connections.items():
            try:
                desc = arcpy.Describe(path)
                status[name] = {
                    "path": path,
                    "connected": True,
                    "workspace_type": desc.workspaceType,
                    "connection_info": getattr(desc, 'connectionString', 'N/A')
                }

            except Exception as e:
                status[name] = {
                    "path": path,
                    "connection": False,
                    "error": str(e)
                }

        return status
    
    def validate_prerequisites(self) -> Tuple[bool, List[str]]:
        """Validate that all required data sources are accessible."""

        issues = []

        # Check connections
        for name, path in [("test_SDE", self.test_SDE), ("prod_SDE", self.prod_SDE),
                           ("oracle_ODC", self.oracle_ODC), ("csa_Prod_SDE", self.csa_Prod_SDE)]:
            
            try:
                arcpy.Describe(path)
            except Exception as e:
                issues.append(f"Cannot connect to {name}: {e}")

        # Check required feature classes exist
        required_fcs = [
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Points",
            f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands",
            f"{self.prod_SDE}\\OWRBGIS.WR_LOOKUP_VALUES",
            f"{self.oracle_ODC}\\WR.WR_STPERMIT"
        ]

        for fc in required_fcs:
            if not arcpy.Exists(fc):
                issues.append(f"Required feature class missing: {fc}")

        return len(issues) == 0, issues
    
    def get_data_counts(self) -> Dict[str, int]:
        """Get record counts for monitoring."""

        datasets = {
            "test_pt_points": f"{self.test_SDE}\\OWRBGIS.WR_PT_Points",
            "test_pt_lands": f"{self.test_SDE}\\OWRBGIS.WR_PT_Lands",
            "prod_pt_points": f"{self.prod_SDE}\\OWRBGIS.WR_PT_Points",
            "prod_pt_lands": f"{self.prod_SDE}\\OWRBGIS.WR_PT_Lands",
            "oracle_permits": f"{self.oracle_ODC}\\WR.WR_STPERMIT"
        }

        counts = {}
        for name, dataset in datasets.items():
            try:
                if arcpy.Exists(dataset):
                    counts[name] = int(arcpy.management.GetCount(dataset)[0])
                else:
                    counts[name] = -1 # Dataset doesn't exist
            except Exception as e:
                logger.warning(f"Could not get count for {name}: {e}")
                counts[name] = -2 # Error getting count

        return counts
