"""Raw, append-only ingestion adapters for files and HTTP APIs."""

from dataflow_ai.ingestion.api_loader import ApiIngestor
from dataflow_ai.ingestion.csv_loader import CsvIngestor
from dataflow_ai.ingestion.flexible_sales import (
    FlexibleSalesImport,
    SalesImportError,
    SalesImportProfile,
    SUPPORTED_SALES_EXTENSIONS,
    is_supported_sales_file,
    load_flexible_sales_files,
)
from dataflow_ai.ingestion.json_loader import JsonIngestor
from dataflow_ai.ingestion.raw_store import RawDataLake

__all__ = [
    "ApiIngestor",
    "CsvIngestor",
    "FlexibleSalesImport",
    "JsonIngestor",
    "RawDataLake",
    "SalesImportError",
    "SalesImportProfile",
    "SUPPORTED_SALES_EXTENSIONS",
    "is_supported_sales_file",
    "load_flexible_sales_files",
]

