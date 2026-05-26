"""
ActivityWatch Sync Tool
Syncs bucket data between ActivityWatch instances on different devices.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class SyncClient:
    """Client for syncing ActivityWatch data between devices."""
    
    def __init__(self, source_url: str, target_url: str, api_key: Optional[str] = None):
        self.source_url = source_url.rstrip("/")
        self.target_url = target_url.rstrip("/")
        self.headers = {}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
    
    def _get(self, url: str) -> dict:
        resp = requests.get(url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    
    def _post(self, url: str, data: dict) -> dict:
        resp = requests.post(url, json=data, headers=self.headers, timeout=60)
        resp.raise_for_status()
        return resp.json()
    
    def export_from_source(self) -> dict:
        """Export all data from the source server."""
        logger.info(f"Exporting from {self.source_url}")
        return self._get(f"{self.source_url}/api/0/sync/export")
    
    def import_to_target(self, data: dict) -> dict:
        """Import data to the target server."""
        logger.info(f"Importing to {self.target_url}")
        result = self._post(f"{self.target_url}/api/0/sync/import", data)
        logger.info(f"Imported {result.get('imported', 0)} events, skipped {result.get('skipped', 0)}")
        return result
    
    def sync(self) -> dict:
        """Full sync: export from source, import to target."""
        data = self.export_from_source()
        result = self.import_to_target(data)
        result["exported_buckets"] = len(data.get("buckets", {}))
        return result
    
    def status(self, url: Optional[str] = None) -> dict:
        """Get sync status from a server."""
        base = (url or self.source_url).rstrip("/")
        return self._get(f"{base}/api/0/sync/status")


def sync_bidirectional(device_a: str, device_b: str, api_key: Optional[str] = None):
    """Sync data in both directions between two devices."""
    client_ab = SyncClient(device_a, device_b, api_key)
    client_ba = SyncClient(device_b, device_a, api_key)
    
    logger.info("Syncing A -> B")
    result_ab = client_ab.sync()
    
    logger.info("Syncing B -> A")
    result_ba = client_ba.sync()
    
    return {
        "a_to_b": result_ab,
        "b_to_a": result_ba,
    }


def cli():
    """Command-line interface for the sync tool."""
    import argparse
    
    parser = argparse.ArgumentParser(description="ActivityWatch Sync Tool")
    parser.add_argument("command", choices=["sync", "status", "export"], 
                        help="Command to run")
    parser.add_argument("--source", "-s", help="Source server URL")
    parser.add_argument("--target", "-t", help="Target server URL")
    parser.add_argument("--bidirectional", "-b", action="store_true",
                        help="Sync in both directions")
    parser.add_argument("--api-key", "-k", help="API key for authentication")
    
    args = parser.parse_args()
    
    if args.command == "status":
        url = args.source or "http://localhost:5600"
        client = SyncClient(url, url, args.api_key)
        status = client.status(url)
        print(json.dumps(status, indent=2))
    
    elif args.command == "export":
        url = args.source or "http://localhost:5600"
        client = SyncClient(url, url, args.api_key)
        data = client.export_from_source()
        print(json.dumps(data, indent=2))
    
    elif args.command == "sync":
        if not args.source or not args.target:
            print("Error: --source and --target are required for sync")
            return
        
        if args.bidirectional:
            result = sync_bidirectional(args.source, args.target, args.api_key)
        else:
            client = SyncClient(args.source, args.target, args.api_key)
            result = client.sync()
        
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    cli()
