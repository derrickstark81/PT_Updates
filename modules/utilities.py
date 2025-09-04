'''*********************************************************************************************************************************
Tool Name: metadata.py
Version: Python 3.11.10
Author: DAS (OWRB GIS)
Description: 
            Module to reusable utilities for logging, timing, and validation and file operations.

History: 
            Initial coding - DAS 20250617
            Enhanced with advanced utilities - DAS 20250903
            
Usage: Use in main script
Comments: Added performance monitoring, advanced validation, and file operations
************************************************************************************************************************************'''

import time, logging, psutil, json, subprocess, threading, arcpy, shutil, smtplib
from functools import wraps
from typing import Callable, Any, Dict, List, Optional, Tuple
from zipfile import ZipFile, ZIP_DEFLATED
from pathlib import Path
from datetime import datetime
from logging.config import dictConfig
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class PerformanceMonitor:
    """Monitor system performance during operations."""

    def __init__(self):
        self.start_time = None
        self.peak_memory = 0
        self.monitoring = False
        self.monitor_thread = None

    def start_monitoring(self):
        """Start performance monitoring."""
        self.start_time = time.time()
        self.peak_memory = 0
        self.monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_resources)
        self._monitor_thread.daemon = True
        self._monitor_thread.start()

    def stop_monitoring(self) -> Dict[str, Any]:
        """Stop monitoring and return performance report."""
        self.monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1.0)

        duration = time.time() - self.start_time if self.start_time else 0

        return{
            "duration_seconds": round(duration, 2),
            "peak_memory_mb": round(self.peak_memory / (1024 * 1024), 2),
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent
        }
    
    def _monitor_resources(self):
        """Monitor system resources in background thread."""

        while self.monitoring:
            try:
                process = psutil.Process()
                memory_usage = process.memory_full_info().rss
                if memory_usage > self.peak_memory:
                    self.peak_memory = memory_usage
                time.sleep(0.5)
            except Exception:
                break

def timeit(func: Callable) -> Callable:
    '''Enhanced decorator to log function execution time and performance.'''

    @wraps(func)
    def wrapper(*args, **kwargs) -> Any:

        monitor = PerformanceMonitor()
        monitor.start_monitoring()

        start_time = time.time()

        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time

            perf_stats = monitor.stop_monitoring()

            logger.info(
                f"{func.__name__} completed in {duration:.2f}s"
                f"(Peak memory: {perf_stats['peak_memory_mb']}MB)"
            )
        
            return result
        
        except Exception as e:
            duration = time.time() - start_time
            perf_stats = monitor.stop_monitoring()

            logger.error(
                f"{func.__name__} failed after {duration:.2f}s "
                f"(Peak memory: {perf_stats['peak_memory_mb']}MB): {e}"
            )
            raise

    return wrapper

def validate_path(path: str, must_exist: bool = True, create_if_missing: bool = False) -> bool:
    '''Enhanced path validation with creation option.'''

    path_obj = Path(path)

    if path_obj.exists():
        return True
    
    if not must_exist:
        return True
    
    if create_if_missing:
        try:
            if path.endswith('.gdb'):

                # Handle geodatabase creation
                parent_dir = path_obj.parent
                gdb_name = path_obj.stem
                parent_dir.mkdir(parents=True, exist_ok=True)

                arcpy.management.CreateFileGDB(str(parent_dir), f"{gdb_name}.gdb")
                logger.info(f"Created geodatabase: {path}")
                return True
            else:
                
                # Handle regular directory creation
                path_obj.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created directory: {path}")
                return True
            
        except Exception as e:
            logger.error(f"Failed to create path {path}: {e}")
            return False
        
    logger.warning(f"Path does not exist: {path}")
    return False


def zip_files_advanced(src_dir: str, output_zip: str,
                       file_patterns: Optional[List[str]] = None,
                       exclude_patterns: Optional[List[str]] = None) -> bool:
    '''Enhanced file zipping with pattern filtering.'''

    try:
        src_path = Path(src_dir)
        if not src_path.exists():
            logger.error(f"Source directory does not exist: {src_dir}")
            return False
        
        # Ensure output directory exists
        output_path = Path(output_zip)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with ZipFile(output_zip, 'W', ZIP_DEFLATED) as zipf:
            files_added = 0

            # Get all files to zip
            if file_patterns:
                files_to_zip = []
                for pattern in file_patterns:
                    files_to_zip.extend(src_path.glob(pattern))

            else:
                files_to_zip = src_path.rglob("*")

            for file_path in files_to_zip:
                if file_path.is_file():

                    # Check exclude patterns
                    if exclude_patterns:
                        skip = any(file_path.match(pattern) for pattern in exclude_patterns)
                        if skip:
                            continue

                    # Add to zip with relative path
                    relative_path = file_path.relative_to(src_path)
                    zipf.write(file_path, relative_path)
                    files_added += 1

        logger.info(f"Created zip file with {files_added} files: {output_zip}")
        return True
    
    except Exception as e:
        logger.error(f"Zip creation failed: {e}")
        return False
    
def setup_logging(log_dir: str, log_level: str = "INFO") -> logging.Logger:
    """Setup enhanced logging configuration."""

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Create log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"pt_updates_{timestamp}.log"

    # Configure logging
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": "%(asctime)s | %(levelname)-8s | %(name)-20s | %(funcName)-15s | %(message)s"
            },
            "simple": {
                "format": "%(asctime)s - %(levelname)s - %(message)s"
            }
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": str(log_file),
                "mode": "W",
                "formatter": "detailed",
                "level": log_level
            },
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "simple",
                "level": "INFO"
            }
        },
        "root": {
            "level": log_level,
            "handlers": ["file", "console"]
        }
    }

    dictConfig(logging_config)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized - Log file: {log_file}")

    return logger

def validate_arcgis_environment() -> Tuple[bool, List[str]]:
    """Validate ArcGIS Pro environment and licensing."""

    issues = []

    try:

        # Check ArcGIS Pro version
        version_info = arcpy.GetInstallInfo()
        version = version_info.get('Version', 'Unknown')
        product = version_info.get('ProductName', 'Unknown')

        logger.info(f"ArcGIS Product: {product}, Version: {version}")

        # Check for ArcGIS Pro 3.4+ (required by Enterprise 11.x)
        if version and version.startswith('3.'):
            major, minor = version.split('.')[:2]
            if int(major) >= 3 and int(minor) >= 4:
                logger.info("✅ ArcGIS Pro version compatible with Enterprise 11.x")
            else:
                issues.append(f"ArcGIS Pro {version} may not be fully compatible with Enterprise 11.x")

        # Check licensing
        license_level = arcpy.CheckProduct("ArcInfo")
        if license_level == "Available":
            logger.info("✅ ArcGIS Pro Professional Plus license available")
        else:
            license_level = arcpy.CheckProduct("ArcEditor")
            if license_level == "Available":
                logger.info("✅ ArcGIS Pro Professional license available")
            else:
                issues.append("Professional or Professional Plus license required for editing operations")

        # Check extensions if needed
        extensions = ["Spatial", "Network"] # Add as needed
        for ext in extensions:
            if arcpy.CheckExtension(ext) == "Available":
                logger.info(f"✅ {ext} Analyst extension available")
            else:
                logger.warning(f"⚠️ {ext} Analyst extension not available")

    except ImportError:
        issues.append("ArcPy module not available - ensure ArcGIS Pro is installed")
    except Exception as e:
        issues.append(f"ArcGIS environment validation error: {e}")

    return len(issues) == 0, issues

def calculate_dynamic_date_filter(years_back: int = 2) -> str:
    """Calculate dynamic date filter for SQL queries."""

    cutoff_date = datetime.now().replace(year=datetime.now().year - years_back)
    return cutoff_date.strftime("date'%Y-%m-%d 00:00:00'")

def monitor_disk_space(paths: List[str], min_free_gb: float = 5.0) -> Tuple[bool, Dict[str, float]]:
    """Monitor disk space for critical paths"""

    space_info = {}
    all_good = True
    
    for path in paths:
        try:
            path_obj = Path(path)
            if path_obj.exists():
                disk_usage = psutil.disk_usage(str(path_obj.anchor))
                free_gb = disk_usage.free / (1024**3)
                space_info[path] = free_gb

                if free_gb < min_free_gb:
                    logger.warning(f"Low disk space on {path}: {free_gb:.1f}GB free")
                    all_good = False
                else:
                    logger.info(f"Disk space OK on {path}: {free_gb:.1F}GB free")
            else:
                space_info[path] = -1 # Path doesn't exist
                logger.warning(f"Path does not exist for disk space check: {path}")

        except Exception as e:
            space_info[path] = -2 # Error checking
            logger.error(f"Error checking disk space for {path}: {e}")
            all_good = False

    return all_good, space_info

def create_progress_tracker(total_steps: int, operation_name: str = "Operation"):
    """Create a progress tracker for long-running operations."""

    class ProgressTracker:
        def __init__(self, total: int, name: str):
            self.total = total
            self.current = 0
            self.name = name
            self.start_time = time.time()

        def update(self, step_name: str = ""):
            self.current += 1
            elapsed = time.time() - self.start_time

            if self.current > 0:
                avg_time = elapsed / self.current
                eta = avg_time * (self.total - self.current)
                eta_str = f"ETA: {eta/60:.1f}min" if eta > 60 else f"ETA: {eta:.0f}s"
            else:
                eta_str = "ETA: calculating..."

            progress_pct = (self.current / self.total) * 100

            logger.info(
                f"{self.name} Progress: {self.current}/{self.total} ({progress_pct:.1f}%)"
                f"| {step_name} | {eta_str}"
            )

        def complete(self):
            total_time = time.time() - self.start_time
            logger.info(
                f"{self.name} completed in {total_time/60:.1f} minutes "
                f"({total_time/self.total:.1f}s avg per step)"
            )

    return ProgressTracker(total_steps, operation_name)

def validate_database_connections(connections: Dict[str, str]) -> Tuple[bool, Dict[str, bool]]:
    """Validate all database connections."""

    connection_status = {}
    all_connected = True

    for name, path in connections.items():
        try:
            # Test connection by describing the workspace
            desc = arcpy.Describe(path)
            connection_status[name] = True
            logger.info(f"✅ Connected to {name}: {desc.workspaceType}")

        except Exception as e:
            connection_status[name] = False
            all_connected = False
            logger.error(f"❌ Failed to connect to {name} at {path}: {e}")

    return all_connected, connection_status

def backup_existing_data(backup_dir: str, datasets: List[str]) -> bool:
    """Create backup of existing datasets before processing."""
    try:
        backup_path = Path(backup_dir) / datetime.now().strftime("backup_%Y%m%d_%H%M%S")
        backup_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Creating data backup in {backup_path}")

        backed_up = 0

        for dataset in datasets:
            if arcpy.Exists(dataset):
                try:
                    dataset_name = Path(dataset).name
                    backup_dataset = backup_path / f"{dataset_name}.backup"

                    arcpy.management.Copy(dataset, str(backup_dataset))
                    backed_up += 1
                    logger.debug(f"Backed up: {dataset}")

                except Exception as e:
                    logger.warning(f"Count not backup {dataset}: {e}")

        logger.info(f"Backup completed: {backed_up}/{len(datasets)} datasets")
        return backed_up > 0
    
    except Exception as e:
        logger.error(f"Backup operation failed: {e}")
        return False
    
def cleanup_temp_files(temp_patterns: List[str], older_than_hours: int = 24) -> int:
    """Clean up temporary files older than specified hours."""

    cutoff_time = time.time() - (older_than_hours * 3600)
    cleaned_count = 0

    for pattern in temp_patterns:
        try:
            for file_path in Path(".").glob(pattern):
                if file_path.is_file():
                    file_path.unlink()
                    cleaned_count += 1
                    logger.debug(f"Cleaned temp file: {file_path}")
                elif file_path.is_dir():
                    shutil.rmtree(file_path)
                    cleaned_count += 1
                    logger.debug(f"Cleaned temp directory: {file_path}")
        except Exception as e:
            logger.warning(f"Error cleaning pattern {pattern}: {e}")

    if cleaned_count > 0:
        logger.info(f"Cleaned {cleaned_count} temporary files/directories")

    return cleaned_count

def run_system_command(command: str, timeout: int = 300) -> Tuple[bool, str, str]:
    """Run system command with timeout and capture output."""
    try: 
        logger.info(f"Running system command: {command}")

        result = subprocess.run(
            command, shell=True, timeout=timeout,
            capture_output=True, text=True
        )

        success = result.returncode == 0

        if success:
            logger.info("System command completed successfully")
        else:
            logger.error(f"System command failed with return code {result.returncode}")

        return success, result.stdout, result.stderr
    
    except subprocess.TimeoutExpired:
        logger.error(f"System command times out after {timeout} seconds")
        return False, "", "Command times out"
    
    except Exception as e:
        logger.error(f"System command error: {e}")
        return False, "", str(e)
    
def create_execution_report(operation_results: Dict[str, Any],
                            performance_stats: Dict[str, Any], 
                            output_file: Optional[str] = None) -> Dict[str, Any]:
    """Create comprehensive execution report."""

    report = {
        "execution_summary": {
            "timestamp": datetime.now().isoformat(),
            "total_operations": len(operation_results),
            "successful_operations": sum(1 for result in operation_results.values() if result),
            "failed_operations": sum(1 for result in operation_results.values() if not result),
            "success_rate": round(
                sum(1 for result in operation_results.values() if result) / len(operation_results) * 100, 2
            ) if operation_results else 0
        },
        "operation_details": operation_results,
        "performance_stats": performance_stats,
        "system_info": {
            "cpu_count": psutil.cpu_count(),
            "total_memory_gb": round(psutil.virtual_memory().total / (1024**3), 2), 
            "python_version": f"{psutil.version_info.major}.{psutil.version_info.minor}",
            "platform": psutil.Process().platform
        }
    }

    # Save to file if specified
    if output_file:
        try:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'W') as f:
                json.dump(report, f, indent=2, default=str)

            logger.info(f"Execution report saved to: {output_file}")

        except Exception as e:
            logger.error(f"Failed to save execution report: {e}")

    return report

def validate_data_integrity(datasets: Dict[str, str], expected_counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Validate data integrity after operations."""

    integrity_report = {
        "validation_time": datetime.now().isoformat(),
        "datasets": {},
        "overall_status": True
    }

    for name, dataset_path in datasets.items():
        dataset_info = {
            "exists": False,
            "record_count": 0,
            "has_geometry": False,
            "geometry_type": None,
            "spatial_reference": None,
            "issues": []
        }

        try:
            if arcpy.Exists(dataset_path):
                dataset_info["exists"] = True

                # Get record count
                count = int(arcpy.management.GetCount(dataset_path)[0])
                dataset_info["record_count"] = count

                # Check expected count if provided
                if expected_counts and name in expected_counts:
                    expected = expected_counts[name]
                    if count != expected:
                        dataset_info["issues"].append(
                            f"Count mismatch: expected {expected}, got {count}"
                        )
                        integrity_report["overall_status"] = False

                # Check geometry info for feature classes
                desc = arcpy.Describe(dataset_path)
                if hasattr(desc, 'shapeType'):
                    dataset_info["has_geometry"] = True
                    dataset_info["geometry_type"] = desc.shapeType
                    dataset_info["spatial_reference"] = desc.spatialReference.name

                    # Validate spatial reference
                    if not desc.spatialReference.name:
                        dataset_info["issues"].append("Missing spatial reference")
                        integrity_report["overall_status"] = False

            else:
                dataset_info["issues"].append("Dataset does not exist")
                integrity_report["overall_status"] = False

        except Exception as e:
            dataset_info["issues"].append(f"Validation error: {e}")
            integrity_report["overall_status"] = False

        integrity_report["datasets"][name] = dataset_info

    return integrity_report

def send_email_notification(subject: str, body: str, recipients: List[str],
                            smtp_server: str = "smtp.office365.com") -> bool:
    """Send email notification about script completion/failure."""
    try:
        # Note: This would require authentication setup
        # For now, just log the notification
        logger.info(f"Email notification: {subject}")
        logger.info(f"Receipients: {', '.join(recipients)}")
        logger.info(f"Body: {body}")

        # In production, implement actual SMTP sending here
        return True
    
    except Exception as e:
        logger.error(f"Email notification failed: {e}")
        return False
    
class ConfigManager:
    """Advanced configuration management."""

    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.config_data = None
        self._load_config()

    def _load_config(self):
        """Load configuration with validation."""
        try:
            with open(self.config_path, 'r') as f:
                self.config_data = json.load(f)
            logger.info(f"Configuration loaded from {self.config_path}")
        except Exception as e:
            raise ValueError(f"Failed to load configuration: {e}")
        
    def get(self, key_path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'paths.metadata_dir')."""

        keys = key_path.split('.')
        value = self.config_data

        try: 
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
        
    def update_config(self, updates: Dict[str, Any]) -> bool:
        """Update configuration and save to file."""
        try:
            # Deep merge updates
            self._deep_merge(self.config_data, updates)

            # Save back to file
            with open(self.config_path, 'W') as f:
                json.dump(self.config_data, f, indent=2)

            logger.info("Configuration updated and saved")
            return True
        
        except Exception as e:
            logger.error(f"Failed to update configuration: {e}")
            return False
        
    def _deep_merge(self, base: Dict, updates: Dict):
        """Deep merge dictionaries."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

def safe_parallel_execution(tasks: List[Callable], max_workers: int = 4,
                            timeout: Optional[int] = None) -> List[Tuple[bool, Any]]:
    """Safely execute multiple tasks in parallel with timeout."""
    results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        futures = [executor.submit(task) for task in tasks]

        # Collect results with timeout handling
        for i, future in enumerate(futures):
            try:
                result = future.result(timeout=timeout)
                results.append((True, result))
                logger.debug(f"Task {i+1} completed successfully")

            except Exception as e:
                results.append((False, str(e)))
                logger.error(f"Task {i+1} failed: {e}")

    success_count = sum(1 for success, _ in results if success)
    logger.info(f"Parallel execution completed: {success_count}/{len(tasks)} successful")

    return results

# Utility functions for common PT operations
def format_permit_number(permit_num: str) -> str:
    """Standardize permit number formatting."""
    if not permit_num:
        return ""
    
    # Remove any existing formatting
    clean_num = ''.join(filter(str.isalnum, permit_num))

    # Apply standard formatting (adjust as needed)
    if len(clean_num) >= 4:
        return f"{clean_num[:2]}-{clean_num[2:]}"
    
    return clean_num

def validate_coordinate_system(dataset: str, expected_sr_name: str) -> bool:
    """Validate that dataset has expected coordinate system."""

    try:
        desc = arcpy.Describe(dataset)

        if hasattr(desc, 'spatialReference'):
            current_sr = desc.spatialReference.name
            if current_sr == expected_sr_name:
                return True
            else:
                logger.warning(
                    f"Coordinate system mismatch in {dataset}: "
                    f"expected {expected_sr_name}, got {current_sr}"
                )
                return False
        else:
            logger.warning(f"No spatial reference found for {dataset}")
            return False
        
    except Exception as e:
        logger.error(f"Error validating coordinate system for {dataset}: {e}")
        return False
    
# Context manager for ArcGIS environment settings
class ArcGISEnvironment:
    """Context manager for ArcGIS environment settings."""

    def __init__(self, **env_settings):
        self.new_settings = env_settings
        self.old_settings = {}

    def __enter__(self):

        # Save current settings
        for setting, value in self.new_settings.items():
            self.old_settings[setting] = getattr(arcpy.env, setting, None)
            setattr(arcpy.env, setting, value)

        logger.debug(f"Applied ArcGIS environment settings: {self.new_settings}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):

        # Restore original settings
        for setting, value in self.old_settings.items():
            setattr(arcpy.env, setting, value)

        logger.debug("Restored original ArcGIS environment settings")

if __name__ == "__main__":
    # Test utilities
    logger = setup_logging("logs", "DEBUG")

    # Test environment validation
    is_valid, issues = validate_arcgis_environment()
    if is_valid:
        print("✅ ArcGIS environment validation passed")
    else:
        print("❌ ArcGIS environment issues found:")
        for issue in issues:
            print(f" - {issue}")

    # Test progress tracker
    tracker = create_progress_tracker(5, "Test Operation")
    for i in range(5):
        time.sleep(0.1) # Simulate work
        tracker.update(f"Step {i+1}")
    tracker.complete()