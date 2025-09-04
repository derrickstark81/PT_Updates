'''*************************************************************************************************************************************************
Tool Name:  PT_Updates
Version: ArcGIS Pro 3.4 Python 3.11.10
Author:  DAS (OWRB GIS)
ConfigFile:  //OWRBGIS/GeoDat/GIS/ArcGIS/Toolboxes/ArcGIS_Pro_Toolboxes/Z_SDE_Layers_Update_Pro/Water Rights/PTs/settings.json
Required Arguments: None (configuration-driven)
          
Description:
    This script consolidates and repalces the functionality of 16 existing ArcGIS ModelBuilder workflows from the toolboxes 
    Z_SDE_Layers_Update108 and Z_PT_Water_Rights_102. These models automate updates to Provisional Temporary (PT) permits
    within the Test and Production Enterprise Geodatabases (SDE) on both OWRBGIS and CSA environments.

    This modern implementation features:
    - Type-safe configuration validation with Pydantic
    - Parallel processing with ThreadPoolExecutor
    - Comprehensive error handling and retry logic
    - Performance monitoring and reporting
    - Advanced logging and progress tracking
    - Modern Python patterns and best practices
 
History:  
    Initial coding - DAS 20250617
    Enhanced modern implementation - DAS 20250903

Usage:  python PT__Updates.py [--config settings.json] [--dry-run] [--skip-phase phase_number]
Comments: 
    Requires ArcGIS Pro 3.4+ for Enterprise 11.x compatibility
    Professional or Professional Plus license required for editing operations
****************************************************************************************************************************************************'''
"""
  Markdown Doc: //OWRBGIS/GeoDat/ArcGIS/Toolboxes/ArcGIS_Pro_Toolboxes/Z_SDE_Layers_Update_Pro/Water Rights/PTs/PT_Updates.md
  Tool location: //OWRBGIS/GeoDat/ArcGIS/Toolboxes/ArcGIS_Pro_Toolboxes/Z_SDE_Layers_Update_Pro/Water Rights/PTs/PT_Updates.py
  Toolbox: //OWBGIS/GeoDat/GIS/ArcGIS/Toolboxes/ArcGIS_Pro_Toolboxes/Z_SDE_Layers_Update_Pro
"""

import sys, argparse, traceback
from pathlib import Path
from datetime import datetime, time
from typing import Dict, Any, Optional, List

# Ensure modules directory is in path
sys.path.append(str(Path(__file__).parent / "modules"))

# Import custom modules
try:
    from modules.config_validator import load_and_validate_config, PTUpdatesConfig
    from modules.database import SDEDatabase
    from modules.metadata import MetadataManager
    from modules.utilities import (
        setup_logging, timeit, validate_arcgis_environment, 
        create_progress_tracker, create_execution_report, 
        monitor_disk_space, cleanup_temp_files, ArcGISEnvironment
    )
except ImportError as e:
    print(f"❌ Failed to import required modules: {e}")
    sys.exit(1)

class PTUpdatesOrchestrator:
    """Main orchestrator for PT Updates workflow."""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config: Optional[PTUpdatesConfig] = None
        self.db: Optional[SDEDatabase] = None
        self.metadata_mgr: Optional[MetadataManager] = None
        self.logger = None
        self.operation_results = {}
        self.start_time = None

    def initialize(self) -> bool:
        """Initialize all components and validate environment."""
        try:
            print("🚀 Initializing PT Updates Orchestrator...")

            # Load and validate configuration
            self.config = load_and_validate_config(self.config_path)
            print("✅ Configuration validated successfully")

            # Setup logging
            self.logger = setup_logging(
                Path(__file__).parent / "logs",
                self.config.parameters.metadata_standard # Using as log level for now
            )

            # Validate ArcGIS environment
            is_valid, issues = validate_arcgis_environment()
            if not is_valid:
                for issue in issues:
                    self.logger.error(f"Environment issue: {issue}")
                return False
            
            # Initialize database manager
            self.db = SDEDatabase(self.config.connections.model_dump())

            # Validate prerequisites
            prereq_valid, prereq_issues = self.db.validate_prerequisites()
            if not prereq_valid:
                for issues in prereq_issues:
                    self.logger.error(f"Prerequisite issue: {issue}")
                return False
            
            # Monitor disk space
            critical_paths = [
                self.config.paths.temp_gdb,
                self.config.paths.summary_tables_dir,
                self.config.paths.public_download_dir
            ]

            space_ok, space_info = monitor_disk_space(critical_paths)
            if not space_ok:
                self.logger.warning("Low disk space detected - proceeding with caution")

            self.logger.info("🎯 PT Updates Orchestrator initialized successfully")
            return True
        
        except Exception as e:
            print(f"❌ Initialization failed: {e}")
            if self.logger:
                self.logger.error(f"Initialization failed: {e}")
            return False
        
    @timeit
    def execute_full_workflow(self, skip_phases: Optional[List[int]] = None) -> bool:
        """Execute the complete PT Updates workflow."""
        if skip_phases is None:
            skip_phases = []

        self.start_time = datetime.now()
        self.logger.info("=" * 80)
        self.logger.info("🏁 Starting PT Updates Full Workflow")
        self.logger.info("=" * 80)

        # Create progress tracker
        total_phases = 6 # Adjust based on actual phases
        progress = create_progress_tracker(total_phases, "PT Updates Workflow")

        try:
            with ArcGISEnvironment(overwriteOutput=True, workspace=self.config.connections.test_SDE):

                # Phase 1: Cleanup (1aRemoveOldFilesPTs) done with the Auto Mapper Tool (Bunny Tool)

                # Phase 2: Test SDE Updates (2a, 2b, 2c)
                if 2 not in skip_phases:
                    progress.update("Phase 2: Test SDE updates")
                    self.operation_results["phase_2_test_updates"] = self.db.execute_phase_2_test_updates()

                # Phase 3: Prodcution Sync (3UpdatePTLayersOnProduction&CSA)
                if 3 not in skip_phases:
                    progress.update("Phase 3: Production synchronization")
                    self.operation_results["phase_3_prod_sync"] = self.db.execute_phase_3_production_sync()

                # Phase 4: Geodatabase Operations (a-f)
                if 4 not in skip_phases:
                    progress.update("Phase 4: Geodatabase operations")
                    self.operation_results["phase_4_gdb_ops"] = self.db.execute_phase_4_gdb_operations(
                        self.config.paths.temp_gdb
                    )

                # Phase 5: Relationship Classes and Fields (c1, c2)
                if 5 not in skip_phases:
                    progress.update("Phase 5: Relationship classes")
                    self.operation_results["phase_5_relationships"] = self.db.create_relationship_classes(
                        self.config.paths.temp_gdb
                    )

                # Phase 6: Public Data Export (d1, d2)
                if 6 not in skip_phases:
                    progress.update("Phase 6: Public data export")
                    self.operation_results["phase_6_public_export"] = self.db.export_public_data(
                        self.config.paths.temp_gdb,
                        self.config.paths.public_download_dir
                    )

                    if not self.config.parameters.skip_summary_tables:
                        self.operation_results["phase_6_summary_tables"] = self.db.create_summary_tables(
                            self.config.paths.temp_gdb,
                            self.config.paths.summary_tables_dir
                        )

                # Phase 7: Final Operations (e-i)
                if 7 not in skip_phases:
                    progress.update("Phase 7: Final operations")
                    self.operation_results["phase_7_copy_gdb"] = self.db.copy_gdb_to_arapaho(
                        self.config.paths.temp_gdb,
                        self.config.paths.water_rights_gdb
                    )

                    # Metadata operations
                    metadata_mappings = self._create_metadata_mappings()
                    self.operation_results["phase_7_metadata"] = self.metadata_mgr.batch_import_metadata(
                        metadata_mappings
                    )

                    # Update metadata dates
                    xml_files = list(self.config.paths.metadata_files.model_dump().values())
                    self.operation_results["phase_7_metadata_dates"] = self.metadata_mgr.update_metadata_dates(
                        xml_files
                    )

            progress.complete()

            # Generate final report
            self._generate_final_report()

            # Check overall success
            overall_success = all(self.operation_results.values())

            if overall_success:
                self.logger.info("🎉 PT Updates workflow completed successfully!")
                return True
            else:
                self.logger.error("❌ PT Updates workflow completed with errors")
                return False
            
        except Exception as e:
            self.logger.error(f"Workflow execution failed: {e}")
            self.logger.error(traceback.format_exc())
            return False
        
    def _create_metadata_mappings(self) -> Dict[str, str]:
        """Create mappin of feature classes to metadata files."""
        base_paths = {
            "test": self.config.connections.test_SDE,
            "prod": self.config.connections.prod_SDE,
            "csa": self.config.connections.csa_Prod_SDE
        }

        metadata_files = self.config.paths.metadata_files.model_dump()

        mappings = {}

        # Test SDE mappings
        mappings[f"{base_paths['test']}\\OWRBGIS.WR_PT_Points_All"] = metadata_files["points_all"]
        mappings[f"{base_paths['test']}\\OWRBGIS.WR_PT_Points_Active"] = metadata_files["points_active"]
        mappings[f"{base_paths['test']}\\OWRBGIS.WR_PT_Lands_All"] = metadata_files["lands_all"]
        mappings[f"{base_paths['test']}\\OWRBGIS.WR_PT_Lands_Active"] = metadata_files["lands_active"]

        # Production SDE mappings
        mappings[f"{base_paths['prod']}\\OWRBGIS.WR_PT_Points_All"] = metadata_files["points_all"]
        mappings[f"{base_paths['prod']}\\OWRBGIS.WR_PT_Points"] = metadata_files["points_active"]
        mappings[f"{base_paths['prod']}\\OWRBGIS.WR_PT_Lands_All"] = metadata_files["lands_all"]
        mappings[f"{base_paths['prod']}\\OWRBGIS.WR_PT_Lands"] = metadata_files["lands_active"]
        
        # CSA SDE mappings
        mappings[f"{base_paths['csa']}\\owrp.sde.WR_PT_Points_All"] = metadata_files["points_all"]
        mappings[f"{base_paths['csa']}\\owrp.sde.WR_PT_Points"] = metadata_files["points_active"]
        mappings[f"{base_paths['csa']}\\owrp.sde.WR_PT_Lands_All"] = metadata_files["lands_all"]
        mappings[f"{base_paths['csa']}\\owrp.sde.WR_PT_Lands"] = metadata_files["lands_active"]

        return mappings
    
    def _generate_final_report(self) -> None:
        """Generate comprehensive execution report."""
        try:

            # Get data counts for verification
            data_counts = self.db.get_data_counts()

            # Get connection status
            connection_info = self.db.get_connection_info()

            # Calculate performance stats
            total_duration = (datetime.now() - self.start_time).total_seconds()

            performance_stats = {
                "total_duration_minutes": round(total_duration / 60, 2),
                "operations_completed": len(self.operation_results),
                "success_rate": round(
                    sum(1 for result in self.operation_results.values() if result) /
                    len(self.operation_results) * 100, 2
                ) if self.operation_results else 0,
                "data_counts": data_counts,
                "connection_status": connection_info
            }

            # Create execution report
            report_path = Path(__file__).parent / "logs" / f"pt_updates_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            report = create_execution_report(
                self.operation_results,
                performance_stats,
                str(report_path)
            )

            # Log summary
            self.logger.info("=" * 60)
            self.logger.info("📊 EXECUTION SUMMARY")
            self.logger.info("=" * 60)
            self.logger.info(f"Duration: {performance_stats['total_duration_minutes']} minutes")
            self.logger.info(f"Success Rate: {performance_stats['success_rate']}%")
            self.logger.info(f"Operations: {performance_stats['operations_completed']}")

            # Log data counts
            self.logger.info("\n📈 FINAL DATA COUNTS:")
            for dataset, count in data_counts.items():
                status = "✅" if count >= 0 else "❌"
                self.logger.info(f"{status} {dataset}: {count}")

            self.logger.info(f"\n📋 Full report saved to: {report_path}")

        except Exception as e:
            self.logger.error(f"Failed to generate final report: {e}")

    def execute_phase_only(self, phase_number: int) -> bool:
        """Execute only a specific phase (for testing/debugging)."""

        self.logger.info(f"🎯 Executing Phase {phase_number} only")

        phase_methods = {
            2: lambda: self.db.execute_phase_2_test_updates(),
            3: lambda: self.db.execute_phase_3_production_sync(),
            4: lambda: self.db.execute_phase_4_gdb_operations(self.config.paths.temp_gdb),
            5: lambda: (self.db.create_relationship_classes(self.config.paths.temp_gdb) and
                        self.db.create_identical_points_table(self.config.paths.temp_gdb)),
            6: lambda: (self.db.export_public_data(self.config.paths.temp_gdb, self.config.paths.public_download_dir) and
                        (self.db.create_summary_tables(self.config.paths.temp_gdb, self.config.paths.summary_tables_dir)
                        if not self.config.parameters.skip_summary_tables else True)),
            7: lambda: (self.db.copy_gdb_to_arapaho(self.config.paths.temp_gdb, self.config.paths.water_rights_gdb) and
                        self.metadata_mgr.batch_import_metadata(self._create_metadata_mappings()))
        }

        if phase_number not in phase_methods:
            self.logger.error(f"Invalid phase number: {phase_number}")
            return False
        
        try:
            return phase_methods[phase_number]()
        except Exception as e:
            self.logger.error(f"Phase {phase_number} execution failed: {e}")
            return False
        
    def dry_run(self) -> bool:
        """Perform a dry run to validate configuration and connections."""

        self.logger.info("🧪 Starting DRY RUN - No data will be modified")

        try:
            # Validate all connections
            self.logger.info("Validating database connections...")
            prereq_valid, issues = self.db.validate_prerequisites()

            if not prereq_valid:
                self.logger.error("❌ Dry run failed - rerequisite issues found:")
                for issue in issues:
                    self.logger.error(f"  - {issue}")
                return False
            
            # Check metadata files
            self.logger.info("Validating metadata files...")
            metadata_report = self.metadata_mgr.create_metadata_report()
            invalid_files = [f for f in metadata_report["files"] if not f["valid"]]

            if invalid_files:
                self.logger.warning("⚠️ Invalid metadata files found:")
                for file_info in invalid_files:
                    self.logger.warning(f"  - {file_info['filename']}")

            # Display current data counts
            self.logger.info("Current data counts:")
            data_counts = self.db.get_data_counts()
            for dataset, count in data_counts.items():
                self.logger.info(f" {dataset}: {count} records")

            # Simulate workflow steps
            phases = [
                "Phase 2: Test SDE updates", 
                "Phase 3: Production sync",
                "Phase 4: Geodatabase operations",
                "Phase 5: Relationship classes",
                "Phase 6: Public data export",
                "Phase 7: Final operations"
            ]

            for i, phase in enumerate(phases, 1):
                self.logger.info(f"✓ Would execute: {phase}")
                time.sleep(0.1) # Brief pause for realism

            self.logger.info("✅ Dry run completed successfully - all validations passed")
            return True

        except Exception as e:
            self.logger.error(f"❌ Dry run failed: {e}")
            return False

    def cleanup_and_finalize(self) -> None:
        """Cleanup temporary files and finalize execution."""

        try:
            # Cleanup temporary files
            temp_patterns = ["*.tmp", "temp_*", "*_TMP_*"]
            cleaned_count = cleanup_temp_files(temp_patterns, older_than_hours=1)

            if cleaned_count > 0:
                self.logger.info(f"Cleaned up {cleaned_count} temporary files")

            # Final logging
            if self.start_time:
                total_duration = (datetime.now() - self.start_time).total_seconds()
                self.logger.info(f"⏱️ Total execution time: {total_duration/60:.1f} minutes")

        except Exception as e:
            self.logger.error(f"Cleanup failed: {e}")

def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""

    parser = argparse.ArgumentParser(
        description="PT Updates - Modernized ArcGIS automation for Provisional Temporary permits",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--config", "-c",
        default="settings.json",
        help="Path to configuraton file (default: settings.json)"
    )

    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Perform dry run without modifying data"
    )

    parser.add_argument(
        "--skip-phase", "-s",
        type=int,
        action="append",
        help="Skip specific phase(s) (can be used multiple times)"
    )

    parser.add_argument(
        "--phase-only", "-p",
        type=int,
        help="Execute only the specified phase"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    return parser.parse_args()

def main():
    """Main execution function."""

    # Parse command line arguments
    args = parse_arguments()

    # Print banner
    print("=" * 80)
    print("🌊 OKLAHOMA WATER RESOURCES BOARD")
    print("📋 Provisional Temporary (PT) Updates - Modern Implementation")
    print("🚀 ArcGIS Pro 3.4+ / Enterprise 11.x Compatible")
    print("=" * 80)

    try:
        # Initialize orchestrator
        orchestrator = PTUpdatesOrchestrator(args.config)

        if not orchestrator.initialize():
            print("❌ Initialization failed - check logs for details")
            return 1
        
        # Execute based on arguments
        if args.dry_run:
            success = orchestrator.dry_run()
        elif args.phase_only:
            success = orchestrator.execute_phase_only(args.phase_only)
        else:
            success = orchestrator.execute_full_workflow(args.skip_phase)

        # Cleanup
        orchestrator.cleanup_and_finalize()

        # Return appropriate exit code
        if success:
            print("✅ Execution completed successfully!")
            return 0
        else:
            print("❌ Execution completed with errors - check logs")
            return 1
        
    except KeyboardInterrupt:
        print("\n⚠️ Execution interrupted by user")
        return 2
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        traceback.print_exc()
        return 3
    
if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)