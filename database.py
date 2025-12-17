"""
Database models and cache management for phone number lookups
"""

import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional, Dict
import json


class PhoneCache:
    """Manages phone number caching in SQLite"""
    
    def __init__(self, db_path: str = "data/phone_cache.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create phone_cache table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS phone_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone_number TEXT NOT NULL,
                normalized_phone TEXT NOT NULL,
                company_id INTEGER NOT NULL,
                company_name TEXT NOT NULL,
                contact_id INTEGER,
                contact_name TEXT,
                contact_type TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create index on normalized phone for fast lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_normalized_phone 
            ON phone_cache(normalized_phone)
        """)
        
        # Create sync_log table to track sync operations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT NOT NULL,
                records_processed INTEGER DEFAULT 0,
                records_added INTEGER DEFAULT 0,
                records_updated INTEGER DEFAULT 0,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                status TEXT NOT NULL,
                error_message TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def lookup(self, normalized_phone: str) -> List[Dict]:
        """
        Look up cached records for a phone number
        Returns list of matching records (can be multiple)
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                phone_number,
                normalized_phone,
                company_id,
                company_name,
                contact_id,
                contact_name,
                contact_type,
                last_updated
            FROM phone_cache
            WHERE normalized_phone = ?
            ORDER BY last_updated DESC
        """, (normalized_phone,))
        
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return results
    
    def add_or_update(self, phone_number: str, normalized_phone: str, 
                      company_id: int, company_name: str,
                      contact_id: Optional[int] = None,
                      contact_name: Optional[str] = None,
                      contact_type: Optional[str] = None):
        """Add or update a phone cache entry"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if this exact record exists
        cursor.execute("""
            SELECT id FROM phone_cache
            WHERE normalized_phone = ? AND company_id = ? AND contact_id IS ?
        """, (normalized_phone, company_id, contact_id))
        
        existing = cursor.fetchone()
        
        if existing:
            # Update existing record
            cursor.execute("""
                UPDATE phone_cache
                SET phone_number = ?,
                    company_name = ?,
                    contact_name = ?,
                    contact_type = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (phone_number, company_name, contact_name, contact_type, existing[0]))
        else:
            # Insert new record
            cursor.execute("""
                INSERT INTO phone_cache (
                    phone_number, normalized_phone, company_id, company_name,
                    contact_id, contact_name, contact_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (phone_number, normalized_phone, company_id, company_name,
                  contact_id, contact_name, contact_type))
        
        conn.commit()
        conn.close()
    
    def get_cache_stats(self) -> Dict:
        """Get statistics about the cache"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Total cached phone numbers
        cursor.execute("SELECT COUNT(DISTINCT normalized_phone) FROM phone_cache")
        unique_phones = cursor.fetchone()[0]
        
        # Total records (including duplicates for multiple matches)
        cursor.execute("SELECT COUNT(*) FROM phone_cache")
        total_records = cursor.fetchone()[0]
        
        # Oldest and newest records
        cursor.execute("SELECT MIN(last_updated), MAX(last_updated) FROM phone_cache")
        oldest, newest = cursor.fetchone()
        
        # Last sync info
        cursor.execute("""
            SELECT sync_type, completed_at, records_added, records_updated, status
            FROM sync_log
            ORDER BY id DESC
            LIMIT 1
        """)
        last_sync = cursor.fetchone()
        
        conn.close()
        
        return {
            "unique_phones": unique_phones,
            "total_records": total_records,
            "oldest_record": oldest,
            "newest_record": newest,
            "last_sync": {
                "type": last_sync[0] if last_sync else None,
                "completed": last_sync[1] if last_sync else None,
                "added": last_sync[2] if last_sync else 0,
                "updated": last_sync[3] if last_sync else 0,
                "status": last_sync[4] if last_sync else None
            } if last_sync else None
        }
    
    def start_sync(self, sync_type: str = "auto") -> int:
        """Start a sync operation and return sync_id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sync_log (sync_type, started_at, status)
            VALUES (?, datetime('now'), 'running')
        """, (sync_type,))
        
        sync_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return sync_id
    
    def complete_sync(self, sync_id: int, records_processed: int, 
                     records_added: int, records_updated: int,
                     status: str = "completed", error_message: str = None):
        """Complete a sync operation"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE sync_log
            SET records_processed = ?,
                records_added = ?,
                records_updated = ?,
                completed_at = datetime('now'),
                status = ?,
                error_message = ?
            WHERE id = ?
        """, (records_processed, records_added, records_updated, 
              status, error_message, sync_id))
        
        conn.commit()
        conn.close()
    
    def clear_cache(self):
        """Clear all cached data"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM phone_cache")
        conn.commit()
        conn.close()
    
    def get_stale_cache_age(self) -> Optional[int]:
        """Get age of oldest cache entry in hours"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT MIN(last_updated) FROM phone_cache
        """)
        
        oldest = cursor.fetchone()[0]
        conn.close()
        
        if not oldest:
            return None
        
        oldest_dt = datetime.fromisoformat(oldest)
        age_hours = (datetime.now() - oldest_dt).total_seconds() / 3600
        
        return int(age_hours)


class ExtensionAssignments:
    """Manages extension-to-technician assignments in SQLite"""

    def __init__(self, db_path: str = "data/phone_cache.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize extension assignments table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create extension_assignments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extension_assignments (
                extension TEXT PRIMARY KEY,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                member_identifier TEXT,
                assigned_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    def assign_extension(self, extension: str, first_name: str, last_name: str, member_identifier: str = None):
        """Assign an extension to a technician"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO extension_assignments
            (extension, first_name, last_name, member_identifier, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (extension, first_name, last_name, member_identifier))

        conn.commit()
        conn.close()

    def get_assignment(self, extension: str) -> Optional[Dict]:
        """Get the technician assigned to an extension"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT extension, first_name, last_name, member_identifier, assigned_date, updated_at
            FROM extension_assignments
            WHERE extension = ?
        """, (extension,))

        result = cursor.fetchone()
        conn.close()

        return dict(result) if result else None

    def get_all_assignments(self) -> List[Dict]:
        """Get all extension assignments"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT extension, first_name, last_name, member_identifier, assigned_date, updated_at
            FROM extension_assignments
            ORDER BY extension
        """)

        results = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return results

    def remove_assignment(self, extension: str):
        """Remove an extension assignment"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("DELETE FROM extension_assignments WHERE extension = ?", (extension,))

        conn.commit()
        conn.close()

    def get_assigned_names(self) -> set:
        """Get set of all assigned (first_name, last_name) tuples"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("SELECT first_name, last_name FROM extension_assignments")
        results = cursor.fetchall()
        conn.close()

        return set(results)
