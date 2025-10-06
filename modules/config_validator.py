'''*********************************************************************************************************************************
Tool Name: config_validator.py
Version: Python 3.11.10
Author: DAS (OWRB GIS)
Description: 
            Module to validate settings.json using Pydantic with enhanced validation.

History: 
            Initial coding - DAS 20250617
            Enhanced validation - DAS 20250811
            Test and fix issues from dry run - DAS 20250929
            
Usage: Use in main script
Comments: Added comprehensive validation for paths, connections, and parameters
************************************************************************************************************************************'''

import json
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pathlib import Path
from typing import Dict, List, Optional, Union

class ConnectionsConfig(BaseModel):
    test_SDE: str = Field(..., description="Test SDE connection path")
    prod_SDE: str = Field(..., description="Production SDE connection path")
    oracle_ODC: str = Field(..., description="Oracle ODC connection path")
    csa_Prod_SDE: str = Field(..., description="CSA Production SDE connection path")

    @field_validator('*', mode="before")
    @classmethod
    def validate_connection_exists(cls, v):
        """Validate that SDE connection files exist."""

        if not Path(v).exists():
            raise ValueError(f"Connection file does not exist: {v}")
        return v
    
class MetadataFilesConfig(BaseModel):
    points_all: str = "WR_PT_Points_All.xml"
    points_active: str = "WR_PT_Points_Active.xml"
    pt_points: str = "WR_PT_Points.xml"
    lands_all: str = "WR_PT_Lands_All.xml"
    lands_active: str = "WR_PT_Lands_Active.xml"
    pt_lands: str = "WR_PT_Lands.xml"

class DateFiltersConfig(BaseModel):
    default_year_range: int = Field(2, ge=1, le=10)
    current_year: int = Field(2025, ge=2020, le=2030)

class PathsConfig(BaseModel):
    config_path: str = Field(validation_alias=AliasChoices("config_path", "config_Path"))
    temp_gdb: str
    water_rights_gdb: str
    mastercovs_dir: str
    summary_tables_dir: str
    public_download_dir: str
    metadata_dir: str
    metadata_files: MetadataFilesConfig

    @field_validator('temp_gdb', 'water_rights_gdb', mode="before")
    @classmethod
    def validate_gdb_paths(cls, v: str) -> str:
        """Ensure GDB paths end with .gdb"""
        if not v.endswith('.gdb'):
            raise ValueError(f"Geodatabase path must end with .gdb: {v}")
        return str(v)
    
    @field_validator('metadata_dir', 'mastercovs_dir', 'summary_tables_dir', 'public_download_dir', mode="before")
    @classmethod
    def validate_directories(cls, v: str) -> str:
        """Validate that directories exist or can be created."""
        path = Path(v)
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                raise ValueError(f"Cannot create directory {v}: {e}")
        return str(path)
        
class ParametersConfig(BaseModel):
    active_status_codes: List[Optional[str]] = Field(["A", "E", None])
    date_filters: DateFiltersConfig
    thread_count: int = Field(4, ge=1, le=16)
    skip_summary_tables: bool = False
    metadata_standard: str = "ISO 19139"
    force_metadata_update: bool = False

    @field_validator('thread_count', mode="before")
    def validate_thread_count(cls, v):
        """Ensure reasonable thread count for database operations."""
        import os
        max_threads = min(16, os.cpu_count() *2)
        if v > max_threads:
            raise ValueError(f"Thread count {v} exceeds recommended maximum {max_threads}")
        return v
    
class PTUpdatesConfig(BaseModel):
    connections: ConnectionsConfig
    paths: PathsConfig
    parameters: ParametersConfig

    @model_validator(mode="after")
    def validate_metadata_files_exists(self):
        """Validate that all metadata files exist."""
        metadata_dir = Path(self.paths.metadata_dir)
        for _, filename in self.paths.metadata_files.model_dump().items():
            file_path = metadata_dir / filename
            if not file_path.exists():
                raise ValueError(f"Metadata file does not exist: {file_path}")
        return self
    
def load_and_validate_config(config_path: str) -> PTUpdatesConfig:
    """Load and validate the configuration file."""
    try:
        with open(config_path, 'r') as f:
            raw_config = json.load(f)

        # Validate using Pydantic
        config = PTUpdatesConfig(**raw_config)

        # Additional ArcGIS-specific validation
        _validate_arcgis_environment(config)

        return config
    
    except Exception as e:
        raise ValueError(f"Configuration validation failed: {e}")
    
def _validate_arcgis_environment(config: PTUpdatesConfig) -> None:
    """validate ArcGIS environment and connections."""

    # Check ArcGIS Pro installation
    try:
        import arcpy
        arcpy.env.overwriteOutput = True
    except ImportError:
        raise ValueError("ArcPy not available - ensure ArcGIS Pro is installed")
    
    # Test SDE connections
    for name, path in config.connections.model_dump().items():
        try:
            arcpy.Describe(path)
        except Exception as e:
            raise ValueError(f"Cannot connect to {name} at {path}: {e}")
        
    # Validate geodatabase access
    temp_gdb = config.paths.temp_gdb
    if Path(temp_gdb).exists():
        try:
            arcpy.env.workspace = temp_gdb
            arcpy.ListFeatureClasses()
        except Exception as e:
            raise ValueError(f"Cannot access temp geodatabase {temp_gdb}: {e}")
        
if __name__ == "__main__":
    # Test configuration validation
    config_path = "settings.json"
    try:
        config = load_and_validate_config(config_path)
        print("✅ Configuration validation successful!")
        print(f"Connections: {len(config.connections.model_dump())}")
        print(f"Thread count: {config.parameters.thread_count}")
    except Exception as e:
        print(f"❌ Configuration validation failed: {e}")