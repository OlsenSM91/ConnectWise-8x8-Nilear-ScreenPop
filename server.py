"""
8x8 Work Screenpop Integration with ConnectWise & Nilear v2.1
With SQLite caching, multiple match handling, and contact creation workflow
"""

import os
import base64
import asyncio
from typing import Optional, List, Dict
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Query, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
import httpx
from dotenv import load_dotenv

from database import PhoneCache, ExtensionAssignments
from connectwise_api import (
    search_companies,
    get_company_by_id,
    get_company_contacts,
    get_company_tickets,
    add_phone_to_contact,
    create_contact_with_phone,
    create_company_and_contact,
    create_ticket,
    activate_company_finance,
    get_cw_headers as cw_get_cw_headers,
    get_member_by_name,
    get_member_tickets
)

# Load environment variables
load_dotenv()

# Configuration
CW_CLIENT_ID = os.getenv("CW_CLIENT_ID")
CW_PUBLIC_KEY = os.getenv("CW_PUBLIC_API_KEY")
CW_PRIVATE_KEY = os.getenv("CW_PRIVATE_API_KEY")
CW_COMPANY_ID = os.getenv("CW_COMPANY_ID")
CW_BASE_URL = os.getenv("CW_BASE_URL")
NILEAR_BASE_URL = os.getenv("NILEAR_BASE_URL", "https://mtx.link")
SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "4"))

# Internal extension mapping for technician screenpops
INTERNAL_EXTENSIONS = {
    "9401": ("Miguel", "Sahagun"),
    "1337": ("Steven", "Olsen"),
    "9405": ("Roy", "Morla"),
    "1737": ("Brian", "Erk"),
    "1002": ("Ed", "Saenz"),
    "1744": ("Jessica", "James"),
    "1001": ("Katelyn", "Erk"),
    "1738": ("Bart", "Verwilt"),
    "9404": ("Daniel", "Glick")
}

# Special cases where extension name differs from ConnectWise member name
CONNECTWISE_NAME_OVERRIDES = {
    "1001": ("CNS Service", "Desk")  # Katelyn Erk is "CNS Service Desk" in ConnectWise
}

# Ensure data directory exists
Path("data").mkdir(exist_ok=True)

# Initialize cache and extension assignments
cache = PhoneCache("data/phone_cache.db")
extension_assignments = ExtensionAssignments("data/phone_cache.db")

# Create FastAPI app
app = FastAPI(title="8x8 Nilear Screenpop", version="2.1.0")

# Track last sync time
last_sync_time = None


def get_cw_headers():
    """Generate ConnectWise API headers with authentication"""
    auth_string = f"{CW_COMPANY_ID}+{CW_PUBLIC_KEY}:{CW_PRIVATE_KEY}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()
    
    return {
        "Authorization": f"Basic {auth_encoded}",
        "ClientId": CW_CLIENT_ID,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }


def normalize_phone(phone: str) -> str:
    """Normalize phone number by removing non-digit characters"""
    return ''.join(filter(str.isdigit, phone))


async def sync_phone_cache_from_connectwise(sync_type: str = "auto"):
    """
    Sync phone numbers from ConnectWise to local cache
    This can take a while for large databases
    """
    global last_sync_time
    
    print(f"\n{'='*60}")
    print(f"🔄 STARTING CACHE SYNC ({sync_type})")
    print(f"{'='*60}\n")
    
    sync_id = cache.start_sync(sync_type)
    records_processed = 0
    records_added = 0
    records_updated = 0
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            page = 1
            page_size = 100
            has_more = True
            
            while has_more:
                print(f"Syncing page {page}...")
                
                # Fetch contacts from ConnectWise
                url = f"{CW_BASE_URL}/company/contacts"
                params = {
                    "page": page,
                    "pageSize": page_size,
                    "orderBy": "id desc",
                    "fields": "id,firstName,lastName,company,communicationItems"
                }
                
                response = await client.get(
                    url,
                    headers=get_cw_headers(),
                    params=params
                )
                
                if response.status_code != 200:
                    print(f"Error fetching page {page}: {response.status_code}")
                    break
                
                contacts = response.json()
                
                if not contacts or len(contacts) == 0:
                    has_more = False
                    break
                
                # Process each contact
                for contact in contacts:
                    records_processed += 1
                    
                    company = contact.get("company", {})
                    company_id = company.get("id")
                    company_name = company.get("name", "Unknown")
                    
                    # Skip contacts without company
                    if not company_id:
                        continue
                    
                    contact_id = contact.get("id")
                    contact_name = f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip()
                    
                    # Extract phone numbers from communicationItems
                    comm_items = contact.get("communicationItems", [])
                    for item in comm_items:
                        item_type = item.get("type", {}).get("name", "")
                        
                        # Only cache phone numbers (not emails)
                        if item_type in ["Direct", "Cell", "Fax", "Phone", "Mobile"]:
                            phone_value = item.get("value", "")
                            if phone_value:
                                normalized = normalize_phone(phone_value)
                                
                                if len(normalized) >= 10:  # Valid phone number
                                    # Check if this is new or updated
                                    existing = cache.lookup(normalized)
                                    is_new = not any(
                                        r["company_id"] == company_id and r["contact_id"] == contact_id
                                        for r in existing
                                    )
                                    
                                    cache.add_or_update(
                                        phone_number=phone_value,
                                        normalized_phone=normalized,
                                        company_id=company_id,
                                        company_name=company_name,
                                        contact_id=contact_id,
                                        contact_name=contact_name,
                                        contact_type=item_type
                                    )
                                    
                                    if is_new:
                                        records_added += 1
                                    else:
                                        records_updated += 1
                
                print(f"  Processed {len(contacts)} contacts")
                
                # Move to next page
                if len(contacts) < page_size:
                    has_more = False
                else:
                    page += 1
                
                # Limit to prevent infinite loops (safety)
                if page > 100:  # Max 10,000 contacts
                    print("⚠️  Reached page limit (100), stopping sync")
                    break
        
        # Mark sync as completed
        cache.complete_sync(
            sync_id=sync_id,
            records_processed=records_processed,
            records_added=records_added,
            records_updated=records_updated,
            status="completed"
        )
        
        last_sync_time = datetime.now()
        
        print(f"\n✅ SYNC COMPLETED")
        print(f"  Processed: {records_processed} contacts")
        print(f"  Added: {records_added} new records")
        print(f"  Updated: {records_updated} existing records\n")
        
    except Exception as e:
        print(f"\n❌ SYNC FAILED: {str(e)}\n")
        cache.complete_sync(
            sync_id=sync_id,
            records_processed=records_processed,
            records_added=records_added,
            records_updated=records_updated,
            status="failed",
            error_message=str(e)
        )


async def periodic_sync_task():
    """Background task that syncs cache every N hours"""
    global last_sync_time
    
    while True:
        try:
            # Wait for sync interval
            await asyncio.sleep(SYNC_INTERVAL_HOURS * 3600)
            
            print(f"\n⏰ Auto-sync triggered (every {SYNC_INTERVAL_HOURS} hours)")
            await sync_phone_cache_from_connectwise(sync_type="auto")
            
        except Exception as e:
            print(f"Error in periodic sync: {str(e)}")
            await asyncio.sleep(300)  # Wait 5 minutes before retrying


@app.on_event("startup")
async def startup_event():
    """Run on server startup"""
    global last_sync_time
    
    # Check if cache is empty or stale
    stats = cache.get_cache_stats()
    
    if stats["unique_phones"] == 0:
        print("\n⚠️  Cache is empty, running initial sync...")
        asyncio.create_task(sync_phone_cache_from_connectwise(sync_type="initial"))
    elif cache.get_stale_cache_age() and cache.get_stale_cache_age() > SYNC_INTERVAL_HOURS:
        print(f"\n⚠️  Cache is stale ({cache.get_stale_cache_age()} hours old), syncing...")
        asyncio.create_task(sync_phone_cache_from_connectwise(sync_type="startup"))
    else:
        print(f"\n✅ Cache is fresh ({stats['unique_phones']} phones cached)")
    
    # Start periodic sync task
    asyncio.create_task(periodic_sync_task())


@app.get("/", response_class=HTMLResponse)
async def root():
    """Root endpoint with service information"""
    stats = cache.get_cache_stats()
    
    return f"""
    <html>
        <head>
            <title>CNS4U - 8x8 Screenpop Service</title>
            <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
            <style>
                body {{
                    font-family: 'Poppins', sans-serif;
                    max-width: 1000px;
                    margin: 0 auto;
                    padding: 0;
                    background: #f4f4f4;
                    color: #58595a;
                }}
                .header {{
                    background: #545454;
                    padding: 20px 40px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .logo {{
                    max-width: 300px;
                    height: auto;
                }}
                .container {{
                    background: white;
                    padding: 40px;
                    margin: 0;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
                }}
                h1 {{
                    margin-top: 0;
                    color: #333333;
                    font-family: 'Lato', sans-serif;
                    font-weight: 700;
                }}
                .status {{ color: #6bb545; font-weight: 600; }}
                .stats {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                    gap: 15px;
                    margin: 20px 0;
                }}
                .stat-card {{
                    background: #f1f1f1;
                    padding: 15px;
                    border-radius: 8px;
                    border-left: 4px solid #01aeed;
                }}
                .stat-label {{ font-size: 12px; color: #58595a; text-transform: uppercase; font-weight: 600; }}
                .stat-value {{ font-size: 24px; font-weight: 700; color: #333333; }}
                code {{
                    background: #f1f1f1;
                    padding: 4px 12px;
                    border-radius: 4px;
                    font-family: 'Courier New', monospace;
                    color: #01aeed;
                    font-weight: 500;
                }}
                .endpoint {{
                    background: #f1f1f1;
                    padding: 15px;
                    margin: 10px 0;
                    border-left: 4px solid #01aeed;
                    border-radius: 4px;
                }}
                a {{
                    color: #01aeed;
                    text-decoration: none;
                    font-weight: 500;
                }}
                a:hover {{
                    color: #dd2b28;
                    text-decoration: none;
                }}
                h2 {{
                    color: #333333;
                    font-family: 'Lato', sans-serif;
                    font-weight: 700;
                    margin-top: 30px;
                }}
                ul {{
                    line-height: 1.8;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
            </div>
            <div class="container">
                <h1>8x8 Screenpop Service v2.1</h1>
                <p class="status">✓ Service is running with caching and contact creation</p>
                
                <h2>Cache Statistics:</h2>
                <div class="stats">
                    <div class="stat-card">
                        <div class="stat-label">Cached Phone Numbers</div>
                        <div class="stat-value">{stats['unique_phones']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Total Records</div>
                        <div class="stat-value">{stats['total_records']}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Auto-Sync Interval</div>
                        <div class="stat-value">{SYNC_INTERVAL_HOURS}h</div>
                    </div>
                </div>
                
                <h2>New in v2.1:</h2>
                <ul>
                    <li>✨ Company search with autocomplete</li>
                    <li>✨ Add phone to existing contacts</li>
                    <li>✨ Create new contacts</li>
                    <li>✨ Create new companies</li>
                    <li>✨ Auto-activate in finance module</li>
                </ul>
                
                <h2>Available Endpoints:</h2>
                
                <div class="endpoint">
                    <strong>GET /health</strong><br>
                    Health check and cache statistics
                </div>
                
                <div class="endpoint">
                    <strong>GET /screenpop</strong><br>
                    Main screenpop endpoint<br>
                    Parameters: <code>?phone=4084511400</code>
                </div>
                
                <div class="endpoint">
                    <strong>GET /test</strong><br>
                    Test lookup without redirecting<br>
                    Parameters: <code>?phone=4084511400</code>
                </div>
                
                <div class="endpoint">
                    <strong>POST /sync</strong><br>
                    Force cache synchronization<br>
                    <a href="/sync?force=true">Trigger Manual Sync</a>
                </div>
                
                <h2>8x8 Configuration:</h2>
                <p>Set your 8x8 screenpop URL to:</p>
                <code>http://192.168.1.27:1337/screenpop?phone=%%CallerNumber%%</code>
            </div>
        </body>
    </html>
    """


@app.get("/static/{filename}")
async def serve_static(filename: str):
    """Serve static files (logos)"""
    file_map = {
        "logo-darkbg.png": "logo-1024px-darkbg-logo.png",
        "logo-lightbg.png": "logo-1024px-lightbg-logo.png"
    }

    actual_filename = file_map.get(filename)
    if actual_filename:
        file_path = Path(__file__).parent / actual_filename
        if file_path.exists():
            return FileResponse(file_path)

    return JSONResponse({"error": "File not found"}, status_code=404)


@app.get("/health")
async def health_check():
    """Health check endpoint with cache stats"""
    stats = cache.get_cache_stats()
    
    return {
        "status": "healthy",
        "service": "8x8-nilear-screenpop",
        "version": "2.1.0",
        "caching": {
            "enabled": True,
            "unique_phones": stats["unique_phones"],
            "total_records": stats["total_records"],
            "oldest_record": stats["oldest_record"],
            "newest_record": stats["newest_record"],
            "sync_interval_hours": SYNC_INTERVAL_HOURS,
            "last_sync": stats["last_sync"]
        },
        "connectwise": {
            "configured": True,
            "base_url": CW_BASE_URL
        }
    }


@app.get("/sync")
@app.post("/sync")
async def force_sync(background_tasks: BackgroundTasks, force: bool = Query(False)):
    """Force a cache sync"""
    if force:
        background_tasks.add_task(sync_phone_cache_from_connectwise, sync_type="manual")
        return {
            "status": "sync_started",
            "message": "Manual sync has been triggered in the background",
            "sync_type": "manual"
        }
    else:
        return HTMLResponse(content="""
            <html>
                <head>
                    <style>
                        body {
                            font-family: Arial, sans-serif;
                            max-width: 600px;
                            margin: 100px auto;
                            padding: 20px;
                            text-align: center;
                        }
                        .button {
                            display: inline-block;
                            padding: 15px 30px;
                            background: #667eea;
                            color: white;
                            text-decoration: none;
                            border-radius: 8px;
                            font-size: 18px;
                            margin: 10px;
                        }
                        .button:hover {
                            background: #5568d3;
                        }
                        .danger {
                            background: #dc2626;
                        }
                        .danger:hover {
                            background: #b91c1c;
                        }
                    </style>
                </head>
                <body>
                    <h1>Force Cache Sync</h1>
                    <p>This will synchronize all phone numbers from ConnectWise.<br>
                    This may take several minutes depending on your database size.</p>
                    <a href="/sync?force=true" class="button danger">Start Sync Now</a>
                    <a href="/" class="button">Cancel</a>
                </body>
            </html>
        """)


@app.get("/cache/clear")
async def clear_cache():
    """Clear all cached data"""
    cache.clear_cache()
    return {
        "status": "cache_cleared",
        "message": "All cached data has been cleared. A new sync will be triggered on next lookup."
    }


@app.get("/test")
async def test_lookup(phone: str = Query(..., description="Phone number to test")):
    """Test endpoint to verify lookup"""
    normalized = normalize_phone(phone)
    cached_results = cache.lookup(normalized)
    
    result = {
        "phone_number": phone,
        "normalized": normalized,
        "cache_hit": len(cached_results) > 0,
        "cache_results": cached_results
    }
    
    if not cached_results:
        result["message"] = "Not in cache. Run /sync to update cache."
    
    return result


@app.get("/api/companies/search")
async def api_search_companies(q: str = Query(..., min_length=2)):
    """
    Autocomplete API for company search
    Returns JSON array of companies matching query
    """
    companies = await search_companies(q, limit=10)
    
    # Format for autocomplete
    results = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "identifier": c.get("identifier"),
            "city": c.get("city"),
            "state": c.get("state"),
            "phone": c.get("phoneNumber"),
            "label": f"{c.get('name')} - {c.get('city', '')}, {c.get('state', '')}".strip(" -,")
        }
        for c in companies
    ]
    
    return JSONResponse(content=results)


@app.get("/api/companies/{company_id}/contacts")
async def api_get_company_contacts(company_id: int):
    """Get all contacts for a company"""
    contacts = await get_company_contacts(company_id)
    
    # Format contact data
    results = [
        {
            "id": c.get("id"),
            "name": f"{c.get('firstName', '')} {c.get('lastName', '')}".strip(),
            "firstName": c.get("firstName"),
            "lastName": c.get("lastName"),
            "phones": [
                item.get("value")
                for item in c.get("communicationItems", [])
                if item.get("communicationType") == "Phone"
            ]
        }
        for c in contacts
    ]
    
    return JSONResponse(content=results)


@app.post("/api/contacts/{contact_id}/add-phone")
async def api_add_phone_to_contact(
    contact_id: int,
    phone: str = Form(...),
    phone_type: str = Form("Cell")
):
    """Add a phone number to an existing contact"""
    success = await add_phone_to_contact(contact_id, phone, phone_type)
    
    if success:
        # Add to cache
        normalized = normalize_phone(phone)
        # Get contact details for cache
        async with httpx.AsyncClient(timeout=30.0) as client:
            from connectwise_api import get_cw_headers
            url = f"{CW_BASE_URL}/company/contacts/{contact_id}"
            resp = await client.get(url, headers=get_cw_headers())
            
            if resp.status_code == 200:
                contact = resp.json()
                company = contact.get("company", {})
                
                cache.add_or_update(
                    phone_number=phone,
                    normalized_phone=normalized,
                    company_id=company.get("id"),
                    company_name=company.get("name"),
                    contact_id=contact_id,
                    contact_name=f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip(),
                    contact_type=phone_type
                )
        
        return JSONResponse(content={
            "success": True,
            "message": f"Phone {phone} added to contact"
        })
    else:
        return JSONResponse(content={
            "success": False,
            "message": "Failed to add phone to contact"
        }, status_code=500)


@app.post("/api/contacts/create")
async def api_create_contact(
    company_id: int = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(...),
    email: Optional[str] = Form(None),
    phone_type: str = Form("Cell")
):
    """Create a new contact with phone number"""
    contact_id = await create_contact_with_phone(
        company_id=company_id,
        first_name=first_name,
        last_name=last_name,
        phone_number=phone,
        email=email,
        phone_type=phone_type
    )
    
    if contact_id:
        # Add to cache
        normalized = normalize_phone(phone)
        company = await get_company_by_id(company_id)
        
        cache.add_or_update(
            phone_number=phone,
            normalized_phone=normalized,
            company_id=company_id,
            company_name=company.get("name", "Unknown"),
            contact_id=contact_id,
            contact_name=f"{first_name} {last_name}",
            contact_type=phone_type
        )
        
        return JSONResponse(content={
            "success": True,
            "contact_id": contact_id,
            "message": f"Contact created successfully"
        })
    else:
        return JSONResponse(content={
            "success": False,
            "message": "Failed to create contact"
        }, status_code=500)


@app.post("/api/extensions/assign")
async def api_assign_extension(
    extension: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    member_identifier: Optional[str] = Form(None)
):
    """Assign an extension to a technician"""
    print(f"\n📝 Extension Assignment Request:")
    print(f"   Extension: {extension}")
    print(f"   First Name: {first_name}")
    print(f"   Last Name: {last_name}")
    print(f"   Member Identifier: {member_identifier}")

    try:
        extension_assignments.assign_extension(
            extension=extension,
            first_name=first_name,
            last_name=last_name,
            member_identifier=member_identifier
        )

        print(f"✅ Successfully assigned extension {extension} to {first_name} {last_name}")

        return JSONResponse(content={
            "success": True,
            "message": f"Extension {extension} assigned to {first_name} {last_name}"
        })
    except Exception as e:
        print(f"❌ Error assigning extension: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "message": f"Failed to assign extension: {str(e)}"
        }, status_code=500)


@app.post("/api/companies/create")
async def api_create_company(
    name: str = Form(...),
    address: str = Form(...),
    address2: str = Form(""),
    city: str = Form(...),
    state: str = Form(...),
    zip_code: str = Form(...),
    company_phone: str = Form(...),
    territory: str = Form("Main"),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...)
):
    """Create a new company with initial contact"""
    company_id, contact_id = await create_company_and_contact(
        name=name,
        address=address,
        address2=address2,
        city=city,
        state=state,
        zip_code=zip_code,
        company_phone=company_phone,
        territory=territory,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone
    )
    
    if company_id and contact_id:
        # Add to cache
        normalized = normalize_phone(phone)
        
        cache.add_or_update(
            phone_number=phone,
            normalized_phone=normalized,
            company_id=company_id,
            company_name=name,
            contact_id=contact_id,
            contact_name=f"{first_name} {last_name}",
            contact_type="Cell"
        )
        
        return JSONResponse(content={
            "success": True,
            "company_id": company_id,
            "contact_id": contact_id,
            "message": f"Company and contact created successfully"
        })
    else:
        return JSONResponse(content={
            "success": False,
            "message": "Failed to create company and contact"
        }, status_code=500)


@app.post("/api/tickets/create")
async def api_create_ticket(
    company_id: int = Form(...),
    contact_id: int = Form(...),
    summary: str = Form(...),
    description: str = Form(...),
    board: str = Form(...),
    priority: str = Form(...)
):
    """Create a new service ticket"""
    try:
        ticket_id = await create_ticket(
            company_id=company_id,
            contact_id=contact_id,
            summary=summary,
            description=description,
            board_name=board,
            priority_name=priority,
            status_name="New (email)"
        )

        if ticket_id:
            return JSONResponse(content={
                "success": True,
                "ticket_id": ticket_id,
                "message": f"Ticket #{ticket_id} created successfully"
            })
        else:
            return JSONResponse(content={
                "success": False,
                "message": "Failed to create ticket"
            }, status_code=500)

    except Exception as e:
        print(f"Error in api_create_ticket: {str(e)}")
        return JSONResponse(content={
            "success": False,
            "message": str(e)
        }, status_code=500)


@app.get("/company/{company_id}", response_class=HTMLResponse)
async def company_info(company_id: int):
    """Display company information with recent tickets"""
    try:
        # Get company details
        company = await get_company_by_id(company_id)
        if not company:
            return HTMLResponse(
                content=error_page(
                    "Company Not Found",
                    f"Could not find company with ID {company_id}",
                    "The company may have been deleted or the ID is incorrect."
                ),
                status_code=404
            )

        # Get company contacts for ticket creation form
        contacts = await get_company_contacts(company_id)

        # Get company tickets
        tickets = await get_company_tickets(company_id, status_filter="open", limit=25)

        # Build ticket rows HTML
        ticket_rows = ""
        if tickets:
            for ticket in tickets:
                ticket_id = ticket.get("id", "N/A")
                summary = ticket.get("summary", "No summary")
                status = ticket.get("status", {}).get("name", "Unknown")
                priority = ticket.get("priority", {}).get("name", "Normal")
                board = ticket.get("board", {}).get("name", "Unknown")

                # Get assigned contact/resource
                contact_name = ticket.get("contact", {}).get("name", "N/A") if ticket.get("contact") else "N/A"

                # Priority badge color mapping
                priority_color = {
                    "Priority 1 - Emergency Response": "#dc2626",  # Red
                    "Priority 2 - Quick Response": "#ea580c",      # Orange
                    "Priority 3 - Normal Response": "#eab308",     # Yellow
                    "Priority 4 - Schedule Maintenance": "#3b82f6", # Blue
                    "Priority 5 - Next Time": "#10b981",           # Green
                    "Do Not Respond": "#9333ea"                    # Purple
                }.get(priority, "#eab308")

                # Status color
                status_color = "#10b981" if "new" in status.lower() else "#58595a"

                ticket_rows += f"""
                <tr style="border-bottom: 1px solid #e5e7eb;">
                    <td style="padding: 12px; text-align: left;">
                        <a href="https://app.nilear.com/mtx/{ticket_id}"
                           onclick="openTicketPopup('https://app.nilear.com/mtx/{ticket_id}'); return false;"
                           style="color: #01aeed; text-decoration: none; font-weight: 600; cursor: pointer;">
                            #{ticket_id}
                        </a>
                    </td>
                    <td style="padding: 12px; text-align: left;">{summary[:80]}{'...' if len(summary) > 80 else ''}</td>
                    <td style="padding: 12px; text-align: center; white-space: nowrap;">
                        <span style="background: {priority_color}; color: white; padding: 6px 12px; border-radius: 99px; font-size: 11px; font-weight: 600; display: inline-block; white-space: nowrap;">
                            {priority}
                        </span>
                    </td>
                    <td style="padding: 12px; text-align: center; white-space: nowrap;">
                        <span style="background: {status_color}; color: white; padding: 6px 12px; border-radius: 99px; font-size: 11px; font-weight: 600; display: inline-block;">
                            {status}
                        </span>
                    </td>
                    <td style="padding: 12px; text-align: left;">{board}</td>
                    <td style="padding: 12px; text-align: left;">{contact_name}</td>
                </tr>
                """
        else:
            ticket_rows = """
            <tr>
                <td colspan="6" style="padding: 24px; text-align: center; color: #6b7280;">
                    No open tickets found for this company
                </td>
            </tr>
            """

        # Company info
        company_name = company.get("name", "Unknown Company")
        company_phone = company.get("phoneNumber", "N/A")
        company_address = company.get("addressLine1", "")
        company_city = company.get("city", "")
        company_state = company.get("state", "")
        company_zip = company.get("zip", "")
        company_status = company.get("status", {}).get("name", "Unknown")

        full_address = f"{company_address}, {company_city}, {company_state} {company_zip}".strip(", ")

        # Build contact options for ticket form
        contact_options = ""
        for contact in contacts:
            contact_id = contact.get("id")
            first_name = contact.get("firstName", "")
            last_name = contact.get("lastName", "")
            contact_name = f"{first_name} {last_name}".strip()
            contact_options += f'<option value="{contact_id}">{contact_name}</option>'

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>CNS4U - {company_name}</title>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                body {{
                    font-family: 'Poppins', sans-serif;
                    background: #f4f4f4;
                    min-height: 100vh;
                    padding: 0;
                }}
                .logo-header {{
                    background: #545454;
                    padding: 15px 30px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .logo {{
                    max-width: 250px;
                    height: auto;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background: white;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
                }}
                .header {{
                    background: #01aeed;
                    color: white;
                    padding: 30px;
                    border-bottom: 4px solid #dd2b28;
                }}
                .header h1 {{
                    font-size: 28px;
                    margin-bottom: 10px;
                    font-family: 'Lato', sans-serif;
                    font-weight: 700;
                }}
                .company-info {{
                    padding: 20px 30px;
                    background: #f9fafb;
                    border-bottom: 1px solid #e5e7eb;
                }}
                .info-grid {{
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 15px;
                    margin-top: 15px;
                }}
                .info-item {{
                    display: flex;
                    align-items: center;
                    gap: 10px;
                }}
                .info-label {{
                    font-weight: 600;
                    color: #333333;
                }}
                .info-value {{
                    color: #58595a;
                }}
                .content {{
                    padding: 30px;
                }}
                .section-title {{
                    font-size: 20px;
                    font-weight: 700;
                    font-family: 'Lato', sans-serif;
                    color: #333333;
                    margin-bottom: 20px;
                    padding-bottom: 10px;
                    border-bottom: 3px solid #01aeed;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    background: white;
                }}
                th {{
                    background: #f1f1f1;
                    padding: 12px;
                    text-align: left;
                    font-weight: 700;
                    font-family: 'Lato', sans-serif;
                    color: #333333;
                    border-bottom: 2px solid #01aeed;
                }}
                .actions {{
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #e5e7eb;
                    display: flex;
                    gap: 15px;
                    flex-wrap: wrap;
                }}
                .btn {{
                    padding: 12px 24px;
                    border-radius: 99px;
                    text-decoration: none;
                    font-weight: 600;
                    display: inline-block;
                    transition: all 0.3s;
                }}
                .btn-primary {{
                    background: #01aeed;
                    color: white;
                }}
                .btn-primary:hover {{
                    background: #dd2b28;
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(1, 174, 237, 0.3);
                }}
                .btn-secondary {{
                    background: #58595a;
                    color: white;
                }}
                .btn-secondary:hover {{
                    background: #333333;
                }}
            </style>
        </head>
        <body>
            <div class="logo-header">
                <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
            </div>
            <div class="container">
                <div class="header">
                    <h1>{company_name}</h1>
                    <p>ConnectWise ID: {company_id} | Status: {company_status}</p>
                </div>

                <div class="company-info">
                    <div class="info-grid">
                        <div class="info-item">
                            <span class="info-label">Phone:</span>
                            <span class="info-value">{company_phone}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Address:</span>
                            <span class="info-value">{full_address or 'N/A'}</span>
                        </div>
                    </div>
                </div>

                <div class="content">
                    <h2 class="section-title">Open Tickets ({len(tickets)})</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Ticket #</th>
                                <th>Summary</th>
                                <th style="text-align: center;">Priority</th>
                                <th style="text-align: center;">Status</th>
                                <th>Board</th>
                                <th>Contact</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ticket_rows}
                        </tbody>
                    </table>

                    <div class="actions">
                        <button onclick="showTicketForm()" class="btn btn-primary">
                            Create Ticket
                        </button>
                        <a href="https://app.nilear.com/mtx" target="_blank" class="btn btn-secondary">
                            Open Nilear
                        </a>
                        <a href="/" class="btn btn-secondary">
                            Back to Home
                        </a>
                    </div>

                    <!-- Create Ticket Form (Hidden by default) -->
                    <div id="ticket-form" style="display: none; margin-top: 30px; padding: 30px; background: #f9fafb; border-radius: 8px; border: 2px solid #01aeed;">
                        <h3 style="color: #333333; font-family: 'Lato', sans-serif; margin-top: 0;">Create New Ticket</h3>
                        <form id="create-ticket-form" onsubmit="submitTicket(event)">
                            <input type="hidden" name="company_id" value="{company_id}">

                            <div style="margin-bottom: 20px;">
                                <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Contact</label>
                                <select name="contact_id" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                                    <option value="">Select a contact...</option>
                                    {contact_options}
                                </select>
                            </div>

                            <div style="margin-bottom: 20px;">
                                <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Summary</label>
                                <input type="text" name="summary" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                            </div>

                            <div style="margin-bottom: 20px;">
                                <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Description</label>
                                <textarea name="description" rows="4" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif; resize: vertical;"></textarea>
                            </div>

                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;">
                                <div>
                                    <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Board</label>
                                    <select name="board" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                                        <option value="San Jose Professional Services">San Jose Professional Services</option>
                                        <option value="Hollister Professional Services">Hollister Professional Services</option>
                                    </select>
                                </div>

                                <div>
                                    <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Priority</label>
                                    <select name="priority" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                                        <option value="Priority 1 - Emergency Response" style="color: #dc2626;">🔴 Priority 1 - Emergency Response</option>
                                        <option value="Priority 2 - Quick Response" style="color: #ea580c;">🟠 Priority 2 - Quick Response</option>
                                        <option value="Priority 3 - Normal Response" selected style="color: #eab308;">🟡 Priority 3 - Normal Response</option>
                                        <option value="Priority 4 - Schedule Maintenance" style="color: #3b82f6;">🔵 Priority 4 - Schedule Maintenance</option>
                                        <option value="Priority 5 - Next Time" style="color: #10b981;">🟢 Priority 5 - Next Time</option>
                                        <option value="Do Not Respond" style="color: #9333ea;">🟣 Do Not Respond</option>
                                    </select>
                                </div>
                            </div>

                            <div style="display: flex; gap: 15px;">
                                <button type="submit" class="btn btn-primary">Create Ticket</button>
                                <button type="button" onclick="hideTicketForm()" class="btn btn-secondary">Cancel</button>
                            </div>
                        </form>
                        <div id="ticket-message" style="margin-top: 20px;"></div>
                    </div>
                </div>
            </div>

            <script>
                function showTicketForm() {{
                    document.getElementById('ticket-form').style.display = 'block';
                    document.getElementById('ticket-form').scrollIntoView({{ behavior: 'smooth' }});
                }}

                function hideTicketForm() {{
                    document.getElementById('ticket-form').style.display = 'none';
                    document.getElementById('create-ticket-form').reset();
                    document.getElementById('ticket-message').innerHTML = '';
                }}

                async function submitTicket(event) {{
                    event.preventDefault();
                    const form = event.target;
                    const formData = new FormData(form);
                    const messageDiv = document.getElementById('ticket-message');

                    messageDiv.innerHTML = '<p style="color: #01aeed;">Creating ticket...</p>';

                    try {{
                        const response = await fetch('/api/tickets/create', {{
                            method: 'POST',
                            body: formData
                        }});

                        const result = await response.json();

                        if (result.success) {{
                            messageDiv.innerHTML = `<p style="color: #6bb545; font-weight: 600;">✓ ${{result.message}} - Refreshing...</p>`;
                            // Reload immediately to show the new ticket in the list
                            setTimeout(() => {{
                                window.location.reload(true);
                            }}, 500);
                        }} else {{
                            messageDiv.innerHTML = `<p style="color: #dd2b28; font-weight: 600;">✗ ${{result.message}}</p>`;
                        }}
                    }} catch (error) {{
                        messageDiv.innerHTML = `<p style="color: #dd2b28; font-weight: 600;">✗ Error: ${{error.message}}</p>`;
                    }}
                }}

                function openTicketPopup(url) {{
                    // Calculate centered position
                    const width = Math.min(1400, window.screen.width * 0.9);
                    const height = Math.min(900, window.screen.height * 0.9);
                    const left = (window.screen.width - width) / 2;
                    const top = (window.screen.height - height) / 2;

                    // Open popup window with specific features
                    // This shares the browser session unlike iframe
                    const popup = window.open(
                        url,
                        'TicketDetails',
                        `width=${{width}},height=${{height}},left=${{left}},top=${{top}},resizable=yes,scrollbars=yes,status=yes,toolbar=no,menubar=no,location=no`
                    );

                    // Focus the popup window
                    if (popup) {{
                        popup.focus();
                    }}
                }}
            </script>
        </body>
        </html>
        """

        return HTMLResponse(content=html_content)

    except Exception as e:
        print(f"Error in company_info: {str(e)}")
        return HTMLResponse(
            content=error_page(
                "Error Loading Company",
                f"An error occurred while loading company information",
                str(e)
            ),
            status_code=500
        )


@app.get("/screenpop")
async def screenpop(
    request: Request,
    phone: Optional[str] = Query(None),
    CallerNumber: Optional[str] = Query(None)
):
    """Main screenpop endpoint - checks cache for phone number or handles internal extensions"""
    phone_number = phone or CallerNumber

    if not phone_number:
        return HTMLResponse(
            content=error_page(
                "Missing Phone Number",
                "No phone number provided.",
                "Ensure your 8x8 URL includes ?phone=%%CallerNumber%%"
            ),
            status_code=400
        )

    print(f"\n{'='*60}")
    print(f"📞 Screenpop for: {phone_number}")
    print(f"{'='*60}\n")

    # Check if this is a 4-digit internal extension
    is_internal_extension = phone_number.isdigit() and len(phone_number) == 4

    if is_internal_extension:
        # Check database first, then fall back to hardcoded INTERNAL_EXTENSIONS
        db_assignment = extension_assignments.get_assignment(phone_number)

        if db_assignment or phone_number in INTERNAL_EXTENSIONS:
            # Known extension (from database or hardcoded)
            if db_assignment:
                first_name = db_assignment['first_name']
                last_name = db_assignment['last_name']
                print(f"🔧 Internal extension detected (from database): {first_name} {last_name}")
            else:
                first_name, last_name = INTERNAL_EXTENSIONS[phone_number]
                print(f"🔧 Internal extension detected (hardcoded): {first_name} {last_name}")

            # Check if there's a ConnectWise name override
            if phone_number in CONNECTWISE_NAME_OVERRIDES:
                cw_first, cw_last = CONNECTWISE_NAME_OVERRIDES[phone_number]
                print(f"   Using ConnectWise override: {cw_first} {cw_last}")
            else:
                cw_first, cw_last = first_name, last_name

            # Get member info from ConnectWise
            member = await get_member_by_name(cw_first, cw_last)

            if member:
                member_id = member["id"]
                member_identifier = member["identifier"]
                full_name = f"{first_name} {last_name}"

                print(f"✅ Found member: {full_name} (ID: {member_id}, Identifier: {member_identifier})")

                # Redirect to technician tickets page
                return RedirectResponse(url=f"/technician/{member_identifier}?name={full_name}")
            else:
                print(f"❌ Member not found in ConnectWise: {cw_first} {cw_last}")
                # Get unassigned technicians for error page
                from connectwise_api import get_all_members
                all_members = await get_all_members()
                # Combine hardcoded and database-assigned names
                assigned_names = set(INTERNAL_EXTENSIONS.values()) | extension_assignments.get_assigned_names()
                # Filter out inactive members, assigned members, and those with missing names
                unassigned = [
                    m for m in all_members
                    if (m.get('firstName'), m.get('lastName')) not in assigned_names
                    and not m.get('inactiveFlag', False)
                    and m.get('firstName')  # Must have first name
                    and m.get('lastName')   # Must have last name
                ]

                return HTMLResponse(
                    content=unassigned_technicians_error_page(
                        phone_number,
                        f"{first_name} {last_name}",
                        unassigned
                    ),
                    status_code=404
                )
        else:
            # Unknown extension - show list of unassigned technicians
            print(f"🔧 Unknown internal extension: {phone_number}")
            from connectwise_api import get_all_members
            all_members = await get_all_members()
            # Combine hardcoded and database-assigned names
            assigned_names = set(INTERNAL_EXTENSIONS.values()) | extension_assignments.get_assigned_names()
            # Filter out inactive members, assigned members, and those with missing names
            unassigned = [
                m for m in all_members
                if (m.get('firstName'), m.get('lastName')) not in assigned_names
                and not m.get('inactiveFlag', False)
                and m.get('firstName')  # Must have first name
                and m.get('lastName')   # Must have last name
            ]

            return HTMLResponse(
                content=unassigned_technicians_error_page(
                    phone_number,
                    None,
                    unassigned
                ),
                status_code=404
            )

    normalized = normalize_phone(phone_number)
    
    # Check cache
    cached_results = cache.lookup(normalized)
    
    if not cached_results:
        print(f"❌ No cached results for {phone_number}")
        return HTMLResponse(
            content=not_found_page(phone_number, normalized),
            status_code=404
        )
    
    # If single match, redirect directly
    if len(cached_results) == 1:
        result = cached_results[0]
        company_id = result["company_id"]
        company_url = f"/company/{company_id}"

        print(f"✅ Single match - redirecting to: {company_url}")
        print(f"   Company: {result['company_name']}")
        print(f"   Contact: {result['contact_name']}\n")

        return RedirectResponse(url=company_url)

    # Multiple matches - check if they're all from the same company
    unique_companies = set(r["company_id"] for r in cached_results)

    if len(unique_companies) == 1:
        # All contacts are from the same company
        company_id = cached_results[0]["company_id"]
        company_name = cached_results[0]["company_name"]
        print(f"⚠️  Multiple contacts ({len(cached_results)}) at same company - showing contact selection\n")
        print(f"   Company: {company_name}\n")
        return HTMLResponse(
            content=contact_selection_page(phone_number, company_id, company_name, cached_results),
            status_code=200
        )

    # Multiple companies - show company selection page
    print(f"⚠️  Multiple companies ({len(unique_companies)}) - showing company selection page\n")
    return HTMLResponse(
        content=selection_page(phone_number, cached_results),
        status_code=200
    )


@app.get("/select-company/{company_id}")
async def select_company(company_id: int, phone: str = Query(...)):
    """
    Intermediate endpoint when selecting a company from multiple companies.
    Checks if the company has multiple contacts for this phone number.
    """
    normalized = normalize_phone(phone)
    cached_results = cache.lookup(normalized)

    # Filter results for the selected company
    company_contacts = [r for r in cached_results if r["company_id"] == company_id]

    if not company_contacts:
        return HTMLResponse(
            content=error_page(
                "No Contacts Found",
                f"No contacts found for this company with phone {phone}",
                "The data may have been updated. Please try searching again."
            ),
            status_code=404
        )

    company_name = company_contacts[0]["company_name"]

    # If single contact for this company, redirect to company page
    if len(company_contacts) == 1:
        print(f"✅ Single contact for company {company_id} - redirecting to company page")
        return RedirectResponse(url=f"/company/{company_id}")

    # Multiple contacts for this company - show contact selection
    print(f"⚠️  Multiple contacts ({len(company_contacts)}) for company {company_id} - showing contact selection")
    return HTMLResponse(
        content=contact_selection_page(phone, company_id, company_name, company_contacts),
        status_code=200
    )


@app.get("/technician/{member_identifier}")
async def technician_tickets(member_identifier: str, name: str = Query(...)):
    """
    Display assigned tickets for a technician/member
    """
    try:
        # Get tickets assigned to this technician
        tickets = await get_member_tickets(member_identifier, status_filter="open", limit=50)

        return HTMLResponse(
            content=technician_tickets_page(member_identifier, name, tickets),
            status_code=200
        )

    except Exception as e:
        print(f"Error in technician_tickets: {str(e)}")
        return HTMLResponse(
            content=error_page(
                "Error Loading Tickets",
                f"An error occurred while loading tickets for {name}",
                str(e)
            ),
            status_code=500
        )


# HTML page generators

def contact_selection_page(phone_number: str, company_id: int, company_name: str, contacts: List[Dict]) -> str:
    """Generate selection page for multiple contacts at the same company"""

    # Generate contact rows
    contact_rows = ""
    for contact in contacts:
        contact_name = contact["contact_name"]
        contact_type = contact["contact_type"]
        contact_id = contact["contact_id"]

        contact_rows += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 15px;">
                <strong style="font-size: 16px;">{contact_name}</strong>
            </td>
            <td style="padding: 15px; color: #6b7280;">
                {contact_type}
            </td>
            <td style="padding: 15px; text-align: right;">
                <a href="/company/{company_id}" class="button">Select Contact →</a>
            </td>
        </tr>
        """

    return f"""
    <html>
        <head>
            <title>CNS4U - Select Contact</title>
            <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
            <style>
                body {{
                    font-family: 'Poppins', sans-serif;
                    max-width: 900px;
                    margin: 0 auto;
                    padding: 0;
                    background-color: #f4f4f4;
                }}
                .header {{
                    background: #545454;
                    padding: 20px 40px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .logo {{
                    max-width: 300px;
                    height: auto;
                }}
                .container {{
                    background: white;
                    padding: 40px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
                }}
                h1 {{
                    color: #333333;
                    font-family: 'Lato', sans-serif;
                    font-weight: 700;
                    margin-top: 0;
                }}
                .phone {{
                    font-size: 24px;
                    font-weight: 600;
                    color: #01aeed;
                    margin: 20px 0;
                }}
                .company-name {{
                    font-size: 20px;
                    color: #333333;
                    margin: 20px 0;
                    padding: 15px;
                    background: #f9fafb;
                    border-left: 4px solid #01aeed;
                    border-radius: 4px;
                }}
                .info {{
                    color: #58595a;
                    margin: 20px 0;
                    line-height: 1.6;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 30px;
                    background: white;
                    border: 1px solid #e5e7eb;
                    border-radius: 8px;
                    overflow: hidden;
                }}
                th {{
                    background: #f1f1f1;
                    padding: 15px;
                    text-align: left;
                    font-weight: 700;
                    font-family: 'Lato', sans-serif;
                    color: #333333;
                    border-bottom: 2px solid #01aeed;
                }}
                tr:hover {{
                    background: #f9fafb;
                }}
                .button {{
                    display: inline-block;
                    padding: 10px 20px;
                    background: #01aeed;
                    color: white;
                    text-decoration: none;
                    border-radius: 99px;
                    font-weight: 600;
                    transition: all 0.3s;
                    white-space: nowrap;
                }}
                .button:hover {{
                    background: #dd2b28;
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(1, 174, 237, 0.3);
                }}
                .actions {{
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #e5e7eb;
                }}
                .btn-secondary {{
                    display: inline-block;
                    padding: 10px 20px;
                    background: #58595a;
                    color: white;
                    text-decoration: none;
                    border-radius: 99px;
                    font-weight: 600;
                    transition: all 0.3s;
                    margin-right: 10px;
                }}
                .btn-secondary:hover {{
                    background: #333333;
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
            </div>
            <div class="container">
                <h1>Multiple Contacts Found</h1>
                <div class="phone">{phone_number}</div>
                <div class="company-name">
                    <strong>Company:</strong> {company_name}
                </div>
                <p class="info">
                    This phone number is associated with <strong>{len(contacts)} contacts</strong> at this company.
                    Please select which contact this call is for:
                </p>

                <table>
                    <thead>
                        <tr>
                            <th>Contact Name</th>
                            <th>Phone Type</th>
                            <th style="text-align: right;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {contact_rows}
                    </tbody>
                </table>

                <div class="actions">
                    <button onclick="showNewContactForm()" class="button">Create New Contact</button>
                    <a href="/company/{company_id}" class="btn-secondary">View Company Details</a>
                    <a href="/" class="btn-secondary">Back to Home</a>
                </div>

                <!-- Create New Contact Form (Hidden by default) -->
                <div id="new-contact-section" style="display: none; margin-top: 30px; padding: 30px; background: #f9fafb; border-radius: 8px; border: 2px solid #01aeed;">
                    <h3 style="color: #333333; font-family: 'Lato', sans-serif; margin-top: 0;">Create New Contact</h3>
                    <p style="color: #58595a;">Company: <strong>{company_name}</strong></p>

                    <form id="new-contact-form" onsubmit="submitNewContact(event)">
                        <input type="hidden" name="company_id" value="{company_id}">
                        <input type="hidden" name="phone" value="{phone_number}">

                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 15px;">
                            <div style="margin-bottom: 15px;">
                                <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">First Name *</label>
                                <input type="text" name="first_name" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                            </div>
                            <div style="margin-bottom: 15px;">
                                <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Last Name *</label>
                                <input type="text" name="last_name" required style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                            </div>
                        </div>

                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Phone Number *</label>
                            <input type="text" value="{phone_number}" readonly style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; background: #f9fafb; font-family: 'Poppins', sans-serif;">
                        </div>

                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Email</label>
                            <input type="email" name="email" style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                        </div>

                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 8px; font-weight: 600; color: #333333;">Phone Type</label>
                            <select name="phone_type" style="width: 100%; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-family: 'Poppins', sans-serif;">
                                <option value="Cell">Cell</option>
                                <option value="Direct">Direct</option>
                                <option value="Mobile">Mobile</option>
                                <option value="Phone">Phone</option>
                            </select>
                        </div>

                        <div style="display: flex; gap: 15px;">
                            <button type="submit" class="button">Create Contact</button>
                            <button type="button" onclick="hideNewContactForm()" class="btn-secondary">Cancel</button>
                        </div>
                    </form>
                    <div id="contact-message" style="margin-top: 20px;"></div>
                </div>
            </div>

            <script>
                function showNewContactForm() {{
                    document.getElementById('new-contact-section').style.display = 'block';
                    document.getElementById('new-contact-section').scrollIntoView({{ behavior: 'smooth' }});
                }}

                function hideNewContactForm() {{
                    document.getElementById('new-contact-section').style.display = 'none';
                    document.getElementById('new-contact-form').reset();
                    document.getElementById('contact-message').innerHTML = '';
                }}

                async function submitNewContact(event) {{
                    event.preventDefault();
                    const form = event.target;
                    const formData = new FormData(form);
                    const messageDiv = document.getElementById('contact-message');

                    messageDiv.innerHTML = '<p style="color: #01aeed;">Creating contact...</p>';

                    try {{
                        const response = await fetch('/api/contacts/create', {{
                            method: 'POST',
                            body: formData
                        }});

                        const result = await response.json();

                        if (result.success) {{
                            messageDiv.innerHTML = `<p style="color: #6bb545; font-weight: 600;">✓ Contact created successfully! Redirecting...</p>`;
                            setTimeout(() => {{
                                window.location.href = '/company/{company_id}';
                            }}, 1500);
                        }} else {{
                            messageDiv.innerHTML = `<p style="color: #dd2b28; font-weight: 600;">✗ ${{result.message}}</p>`;
                        }}
                    }} catch (error) {{
                        messageDiv.innerHTML = `<p style="color: #dd2b28; font-weight: 600;">✗ Error: ${{error.message}}</p>`;
                    }}
                }}
            </script>
        </body>
    </html>
    """


def selection_page(phone_number: str, results: List[Dict]) -> str:
    """Generate selection page for multiple matches"""
    
    # Group by company
    companies = {}
    for result in results:
        company_id = result["company_id"]
        if company_id not in companies:
            companies[company_id] = {
                "id": company_id,
                "name": result["company_name"],
                "contacts": []
            }
        companies[company_id]["contacts"].append(result)
    
    # Generate HTML rows
    rows_html = ""
    for company_id, company_data in companies.items():
        contacts_list = "<br>".join([
            f"• {c['contact_name']} ({c['contact_type']})"
            for c in company_data["contacts"]
        ])

        company_url = f"/select-company/{company_id}?phone={phone_number}"

        rows_html += f"""
        <tr>
            <td><strong>{company_data['name']}</strong></td>
            <td style="font-size: 14px; color: #6b7280;">{contacts_list}</td>
            <td>
                <a href="{company_url}" class="button">Select Company →</a>
            </td>
        </tr>
        """
    
    return f"""
    <html>
        <head>
            <title>CNS4U - Select Company</title>
            <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
            <style>
                body {{
                    font-family: 'Poppins', sans-serif;
                    max-width: 1000px;
                    margin: 0 auto;
                    padding: 0;
                    background-color: #f4f4f4;
                }}
                .header {{
                    background: #545454;
                    padding: 20px 40px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .logo {{
                    max-width: 300px;
                    height: auto;
                }}
                .container {{
                    background: white;
                    padding: 40px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
                }}
                h1 {{
                    color: #333333;
                    font-family: 'Lato', sans-serif;
                    font-weight: 700;
                    margin-top: 0;
                }}
                .phone {{
                    font-size: 24px;
                    font-weight: 600;
                    color: #01aeed;
                    margin: 20px 0;
                }}
                .info {{
                    color: #58595a;
                    margin: 20px 0;
                    line-height: 1.6;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 30px;
                }}
                th {{
                    background: #f1f1f1;
                    padding: 15px;
                    text-align: left;
                    font-weight: 700;
                    font-family: 'Lato', sans-serif;
                    color: #333333;
                    border-bottom: 2px solid #01aeed;
                }}
                td {{
                    padding: 20px 15px;
                    border-bottom: 1px solid #e5e7eb;
                }}
                tr:hover {{
                    background: #f9fafb;
                }}
                .button {{
                    display: inline-block;
                    padding: 10px 20px;
                    background: #01aeed;
                    color: white;
                    text-decoration: none;
                    border-radius: 99px;
                    font-weight: 600;
                    transition: all 0.3s;
                }}
                .button:hover {{
                    background: #dd2b28;
                    transform: translateY(-2px);
                }}
            </style>
        </head>
        <body>
            <div class="header">
                <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
            </div>
            <div class="container">
                <h1>Multiple Companies Found</h1>
                <div class="phone">{phone_number}</div>
                <p class="info">
                    This phone number is associated with <strong>{len(companies)} companies</strong>.
                    Please select which client you want to open:
                </p>
                
                <table>
                    <thead>
                        <tr>
                            <th>Company</th>
                            <th>Contacts</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
        </body>
    </html>
    """


def technician_tickets_page(member_identifier: str, name: str, tickets: List[Dict]) -> str:
    """Generate page showing technician's assigned tickets"""

    # Build ticket rows HTML
    ticket_rows = ""
    if tickets:
        for ticket in tickets:
            ticket_id = ticket.get("id", "N/A")
            summary = ticket.get("summary", "No summary")
            status = ticket.get("status", {}).get("name", "Unknown")
            priority = ticket.get("priority", {}).get("name", "Normal")
            board = ticket.get("board", {}).get("name", "Unknown")
            company = ticket.get("company", {}).get("name", "Unknown")

            # Priority badge color mapping
            priority_color = {
                "Priority 1 - Emergency Response": "#dc2626",
                "Priority 2 - Quick Response": "#ea580c",
                "Priority 3 - Normal Response": "#eab308",
                "Priority 4 - Schedule Maintenance": "#3b82f6",
                "Priority 5 - Next Time": "#10b981",
                "Do Not Respond": "#9333ea"
            }.get(priority, "#eab308")

            # Status color
            status_color = "#10b981" if "new" in status.lower() else "#58595a"

            ticket_rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 12px; text-align: left;">
                    <a href="https://app.nilear.com/mtx/{ticket_id}"
                       onclick="openTicketPopup('https://app.nilear.com/mtx/{ticket_id}'); return false;"
                       style="color: #01aeed; text-decoration: none; font-weight: 600; cursor: pointer;">
                        #{ticket_id}
                    </a>
                </td>
                <td style="padding: 12px; text-align: left;">{summary[:80]}{'...' if len(summary) > 80 else ''}</td>
                <td style="padding: 12px; text-align: left;">{company}</td>
                <td style="padding: 12px; text-align: center; white-space: nowrap;">
                    <span style="background: {priority_color}; color: white; padding: 6px 12px; border-radius: 99px; font-size: 11px; font-weight: 600; display: inline-block; white-space: nowrap;">
                        {priority}
                    </span>
                </td>
                <td style="padding: 12px; text-align: center; white-space: nowrap;">
                    <span style="background: {status_color}; color: white; padding: 6px 12px; border-radius: 99px; font-size: 11px; font-weight: 600; display: inline-block;">
                        {status}
                    </span>
                </td>
                <td style="padding: 12px; text-align: left;">{board}</td>
            </tr>
            """
    else:
        ticket_rows = """
        <tr>
            <td colspan="6" style="padding: 24px; text-align: center; color: #6b7280;">
                No open tickets assigned to you
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>CNS4U - {name} Tickets</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: 'Poppins', sans-serif;
                background: #f4f4f4;
                min-height: 100vh;
                padding: 0;
            }}
            .logo-header {{
                background: #545454;
                padding: 15px 30px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            .logo {{
                max-width: 250px;
                height: auto;
            }}
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                background: white;
                box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            }}
            .header {{
                background: #01aeed;
                color: white;
                padding: 30px;
                border-bottom: 4px solid #dd2b28;
            }}
            .header h1 {{
                font-size: 28px;
                margin-bottom: 10px;
                font-family: 'Lato', sans-serif;
                font-weight: 700;
            }}
            .tech-info {{
                padding: 20px 30px;
                background: #f9fafb;
                border-bottom: 1px solid #e5e7eb;
            }}
            .content {{
                padding: 30px;
            }}
            .section-title {{
                font-size: 20px;
                font-weight: 700;
                font-family: 'Lato', sans-serif;
                color: #333333;
                margin-bottom: 20px;
                padding-bottom: 10px;
                border-bottom: 3px solid #01aeed;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: white;
            }}
            th {{
                background: #f1f1f1;
                padding: 12px;
                text-align: left;
                font-weight: 700;
                font-family: 'Lato', sans-serif;
                color: #333333;
                border-bottom: 2px solid #01aeed;
            }}
            .actions {{
                margin-top: 30px;
                padding-top: 20px;
                border-top: 1px solid #e5e7eb;
                display: flex;
                gap: 15px;
                flex-wrap: wrap;
            }}
            .btn {{
                padding: 12px 24px;
                border-radius: 99px;
                text-decoration: none;
                font-weight: 600;
                display: inline-block;
                transition: all 0.3s;
            }}
            .btn-secondary {{
                background: #58595a;
                color: white;
            }}
            .btn-secondary:hover {{
                background: #333333;
            }}
        </style>
    </head>
    <body>
        <div class="logo-header">
            <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
        </div>
        <div class="container">
            <div class="header">
                <h1>{name}'s Assigned Tickets</h1>
                <p>Technician ID: {member_identifier}</p>
            </div>

            <div class="tech-info">
                <div style="display: flex; align-items: center; gap: 20px;">
                    <div>
                        <span style="font-weight: 600; color: #333333;">Open Tickets:</span>
                        <span style="color: #58595a; font-size: 18px; font-weight: 700; margin-left: 10px;">{len(tickets)}</span>
                    </div>
                </div>
            </div>

            <div class="content">
                <h2 class="section-title">Open Tickets ({len(tickets)})</h2>
                <table>
                    <thead>
                        <tr>
                            <th>Ticket #</th>
                            <th>Summary</th>
                            <th>Company</th>
                            <th style="text-align: center;">Priority</th>
                            <th style="text-align: center;">Status</th>
                            <th>Board</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ticket_rows}
                    </tbody>
                </table>

                <div class="actions">
                    <a href="https://app.nilear.com/mtx" target="_blank" class="btn btn-secondary">
                        Open Nilear
                    </a>
                    <a href="/" class="btn btn-secondary">
                        Back to Home
                    </a>
                </div>
            </div>
        </div>

        <script>
            function openTicketPopup(url) {{
                // Calculate centered position
                const width = Math.min(1400, window.screen.width * 0.9);
                const height = Math.min(900, window.screen.height * 0.9);
                const left = (window.screen.width - width) / 2;
                const top = (window.screen.height - height) / 2;

                // Open popup window with specific features
                // This shares the browser session unlike iframe
                const popup = window.open(
                    url,
                    'TicketDetails',
                    `width=${{width}},height=${{height}},left=${{left}},top=${{top}},resizable=yes,scrollbars=yes,status=yes,toolbar=no,menubar=no,location=no`
                );

                // Focus the popup window
                if (popup) {{
                    popup.focus();
                }}
            }}
        </script>
    </body>
    </html>
    """


def unassigned_technicians_error_page(extension: str, searched_name: Optional[str], unassigned_techs: List[Dict]) -> str:
    """Generate error page for invalid extensions showing unassigned technicians"""

    tech_rows = ""
    if unassigned_techs:
        for tech in unassigned_techs:
            first = tech.get('firstName', '')
            last = tech.get('lastName', '')
            identifier = tech.get('identifier', '')
            email = tech.get('officeEmail', '')

            tech_rows += f"""
            <tr style="border-bottom: 1px solid #e5e7eb;">
                <td style="padding: 12px;">{first} {last}</td>
                <td style="padding: 12px;">{identifier}</td>
                <td style="padding: 12px;">{email}</td>
                <td style="padding: 12px; text-align: center;">
                    <button class="assign-btn"
                            data-extension="{extension}"
                            data-firstname="{first}"
                            data-lastname="{last}"
                            data-identifier="{identifier}"
                            onclick="assignExtension(this)">
                        Assign Extension {extension}
                    </button>
                </td>
            </tr>
            """
    else:
        tech_rows = """
        <tr>
            <td colspan="4" style="padding: 24px; text-align: center; color: #6b7280;">
                All technicians are already assigned to extensions
            </td>
        </tr>
        """

    search_info = f"Extension <strong>{extension}</strong> is not assigned"
    if searched_name:
        search_info += f" (searched for: {searched_name})"

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>CNS4U - Extension Not Found</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: 'Poppins', sans-serif;
                background: #f4f4f4;
                min-height: 100vh;
            }}
            .header {{
                background: #545454;
                padding: 20px 40px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            .logo {{
                max-width: 300px;
                height: auto;
            }}
            .container {{
                max-width: 900px;
                margin: 40px auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .error-header {{
                background: #f59e0b;
                color: white;
                padding: 30px 40px;
                border-bottom: 4px solid #d97706;
            }}
            .error-header h1 {{
                font-family: 'Lato', sans-serif;
                font-weight: 700;
                font-size: 28px;
                margin: 0;
            }}
            .content {{
                padding: 40px;
            }}
            .message {{
                color: #4b5563;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 20px;
            }}
            .info-box {{
                background: #fef3c7;
                border-left: 4px solid #f59e0b;
                padding: 15px;
                margin: 20px 0;
                border-radius: 4px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 20px;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                overflow: hidden;
            }}
            th {{
                background: #f1f1f1;
                padding: 12px;
                text-align: left;
                font-weight: 700;
                font-family: 'Lato', sans-serif;
                color: #333333;
                border-bottom: 2px solid #01aeed;
            }}
            .actions {{
                margin-top: 30px;
                padding-top: 30px;
                border-top: 1px solid #e5e7eb;
            }}
            .btn {{
                display: inline-block;
                padding: 12px 24px;
                background: #01aeed;
                color: white;
                text-decoration: none;
                border-radius: 99px;
                font-weight: 600;
                transition: all 0.3s;
            }}
            .btn:hover {{
                background: #dd2b28;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(1, 174, 237, 0.3);
            }}
            .assign-btn {{
                padding: 8px 16px;
                background: #10b981;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 14px;
                cursor: pointer;
                transition: all 0.3s;
            }}
            .assign-btn:hover {{
                background: #059669;
                transform: translateY(-1px);
                box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
            }}
            .assign-btn:disabled {{
                background: #9ca3af;
                cursor: not-allowed;
                transform: none;
            }}
        </style>
        <script>
            async function assignExtension(button) {{
                // Get data from button attributes
                const extension = button.dataset.extension;
                const firstName = button.dataset.firstname;
                const lastName = button.dataset.lastname;
                const identifier = button.dataset.identifier || '';

                // Debug logging
                console.log('Assignment data:', {{
                    extension: extension,
                    firstName: firstName,
                    lastName: lastName,
                    identifier: identifier
                }});

                // Validation
                if (!extension || !firstName || !lastName) {{
                    alert('Missing required data. Please refresh and try again.');
                    return;
                }}

                button.disabled = true;
                button.textContent = 'Assigning...';

                try {{
                    const formData = new FormData();
                    formData.append('extension', extension);
                    formData.append('first_name', firstName);
                    formData.append('last_name', lastName);
                    if (identifier) {{
                        formData.append('member_identifier', identifier);
                    }}

                    console.log('Sending request to /api/extensions/assign');

                    const response = await fetch('/api/extensions/assign', {{
                        method: 'POST',
                        body: formData
                    }});

                    console.log('Response status:', response.status);

                    if (response.ok) {{
                        const result = await response.json();
                        if (result.success) {{
                            button.textContent = '✓ Assigned';
                            button.style.background = '#01aeed';
                            setTimeout(() => {{
                                window.location.href = '/screenpop?phone=' + extension;
                            }}, 1000);
                        }} else {{
                            alert('Failed to assign extension: ' + result.message);
                            button.disabled = false;
                            button.textContent = 'Assign Extension ' + extension;
                        }}
                    }} else {{
                        const errorText = await response.text();
                        console.error('Server error:', errorText);
                        alert('Server error (' + response.status + '). Check console for details.');
                        button.disabled = false;
                        button.textContent = 'Assign Extension ' + extension;
                    }}
                }} catch (error) {{
                    console.error('Error:', error);
                    alert('Error assigning extension: ' + error.message);
                    button.disabled = false;
                    button.textContent = 'Assign Extension ' + extension;
                }}
            }}
        </script>
    </head>
    <body>
        <div class="header">
            <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
        </div>
        <div class="container">
            <div class="error-header">
                <h1>⚠️ Extension Not Found</h1>
            </div>
            <div class="content">
                <p class="message">{search_info}</p>
                <div class="info-box">
                    <strong>Note:</strong> Below are technicians in ConnectWise who don't currently have an extension assigned.
                    Contact your system administrator to assign extension {extension} to a technician.
                </div>

                <h3 style="margin-top: 30px; margin-bottom: 15px; font-family: 'Lato', sans-serif;">Unassigned Technicians</h3>
                <table>
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Identifier</th>
                            <th>Email</th>
                            <th style="text-align: center;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        {tech_rows}
                    </tbody>
                </table>

                <div class="actions">
                    <a href="/" class="btn">← Back to Home</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """


def error_page(title: str, message: str, detail: str = "") -> str:
    """Generate error page HTML with proper branding"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>CNS4U - {title}</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: 'Poppins', sans-serif;
                background: #f4f4f4;
                min-height: 100vh;
            }}
            .header {{
                background: #545454;
                padding: 20px 40px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }}
            .logo {{
                max-width: 300px;
                height: auto;
            }}
            .container {{
                max-width: 800px;
                margin: 40px auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
            .error-header {{
                background: #dc2626;
                color: white;
                padding: 30px 40px;
                border-bottom: 4px solid #991b1b;
            }}
            .error-header h1 {{
                font-family: 'Lato', sans-serif;
                font-weight: 700;
                font-size: 28px;
                margin: 0;
            }}
            .content {{
                padding: 40px;
            }}
            .message {{
                color: #4b5563;
                font-size: 18px;
                line-height: 1.6;
                margin-bottom: 20px;
            }}
            .detail {{
                color: #6b7280;
                font-size: 14px;
                padding: 20px;
                background: #fef2f2;
                border-left: 4px solid #dc2626;
                border-radius: 4px;
                margin-top: 20px;
                font-family: monospace;
            }}
            .actions {{
                margin-top: 30px;
                padding-top: 30px;
                border-top: 1px solid #e5e7eb;
            }}
            .btn {{
                display: inline-block;
                padding: 12px 24px;
                background: #01aeed;
                color: white;
                text-decoration: none;
                border-radius: 99px;
                font-weight: 600;
                transition: all 0.3s;
            }}
            .btn:hover {{
                background: #dd2b28;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(1, 174, 237, 0.3);
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
        </div>
        <div class="container">
            <div class="error-header">
                <h1>⚠️ {title}</h1>
            </div>
            <div class="content">
                <p class="message">{message}</p>
                {f'<div class="detail"><strong>Details:</strong><br>{detail}</div>' if detail else ''}
                <div class="actions">
                    <a href="/" class="btn">← Back to Home</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """


def not_found_page(phone_number: str, normalized: str) -> str:
    """Generate enhanced not found page with company search and creation"""
    return f"""
    <html>
        <head>
            <title>CNS4U - Contact Not Found</title>
            <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&family=Lato:wght@700&display=swap" rel="stylesheet">
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                body {{
                    font-family: 'Poppins', sans-serif;
                    background: #f4f4f4;
                    min-height: 100vh;
                }}
                .logo-header {{
                    background: #545454;
                    padding: 15px 30px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .logo {{
                    max-width: 250px;
                    height: auto;
                }}
                .container {{
                    max-width: 900px;
                    margin: 0 auto;
                    background: white;
                    padding: 40px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.05);
                }}
                h1 {{
                    color: #dd2b28;
                    font-family: 'Lato', sans-serif;
                    margin-top: 0;
                    font-size: 28px;
                }}
                .phone {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #01aeed;
                    margin: 20px 0;
                }}
                .section {{
                    margin: 30px 0;
                    padding: 20px;
                    background: #f9fafb;
                    border-radius: 8px;
                    border-left: 4px solid #01aeed;
                }}
                .section h2 {{
                    margin-top: 0;
                    color: #333333;
                    font-size: 18px;
                    font-family: 'Lato', sans-serif;
                    font-weight: 700;
                }}
                input, select {{
                    width: 100%;
                    padding: 10px;
                    margin: 10px 0;
                    border: 1px solid #d1d5db;
                    border-radius: 6px;
                    font-size: 14px;
                    box-sizing: border-box;
                    font-family: 'Poppins', sans-serif;
                }}
                input:focus, select:focus {{
                    outline: none;
                    border-color: #01aeed;
                    box-shadow: 0 0 0 3px rgba(1, 174, 237, 0.1);
                }}
                button {{
                    padding: 12px 24px;
                    background: #01aeed;
                    color: white;
                    border: none;
                    border-radius: 99px;
                    font-size: 14px;
                    font-weight: 600;
                    cursor: pointer;
                    margin: 5px;
                    transition: all 0.3s;
                    font-family: 'Poppins', sans-serif;
                }}
                button:hover {{
                    background: #dd2b28;
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(1, 174, 237, 0.3);
                }}
                button.secondary {{
                    background: #58595a;
                }}
                button.secondary:hover {{
                    background: #333333;
                }}
                .hidden {{
                    display: none;
                }}
                .autocomplete-results {{
                    position: absolute;
                    width: 100%;
                    max-height: 200px;
                    overflow-y: auto;
                    background: white;
                    border: 1px solid #d1d5db;
                    border-top: none;
                    border-radius: 0 0 6px 6px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    z-index: 1000;
                }}
                .autocomplete-item {{
                    padding: 10px;
                    cursor: pointer;
                    border-bottom: 1px solid #f3f4f6;
                }}
                .autocomplete-item:hover {{
                    background: #f3f4f6;
                }}
                .autocomplete-container {{
                    position: relative;
                }}
                .contact-list {{
                    margin-top: 20px;
                }}
                .contact-item {{
                    padding: 15px;
                    background: white;
                    border: 1px solid #e5e7eb;
                    border-radius: 6px;
                    margin: 10px 0;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }}
                .loading {{
                    text-align: center;
                    color: #6b7280;
                    padding: 20px;
                }}
                .form-grid {{
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 15px;
                }}
                .form-group {{
                    margin: 10px 0;
                }}
                .form-group label {{
                    display: block;
                    margin-bottom: 5px;
                    color: #374151;
                    font-weight: 500;
                    font-size: 14px;
                }}
                .success {{
                    padding: 15px;
                    background: #10b981;
                    color: white;
                    border-radius: 6px;
                    margin: 20px 0;
                }}
                .error {{
                    padding: 15px;
                    background: #dd2b28;
                    color: white;
                    border-radius: 6px;
                    margin: 20px 0;
                }}
                a {{
                    color: #01aeed;
                    text-decoration: none;
                    font-weight: 500;
                }}
                a:hover {{
                    color: #dd2b28;
                }}
            </style>
        </head>
        <body>
            <div class="logo-header">
                <img src="/static/logo-darkbg.png" alt="CNS4U Logo" class="logo" onerror="this.style.display='none'">
            </div>
            <div class="container">
                <h1>🔍 Contact Not Found</h1>
                <div class="phone">{phone_number}</div>
                <p style="color: #6b7280;">Normalized: <code>{normalized}</code></p>
                
                <div id="message"></div>
                
                <!-- Step 1: Search for existing company -->
                <div class="section" id="search-section">
                    <h2>Search for Existing Company</h2>
                    <p>Start typing to search for a company...</p>
                    <div class="autocomplete-container">
                        <input 
                            type="text" 
                            id="company-search" 
                            placeholder="Type company name..."
                            autocomplete="off"
                        />
                        <div id="autocomplete-results" class="autocomplete-results hidden"></div>
                    </div>
                    <button onclick="showNewCompanyForm()">Or Create New Company</button>
                </div>
                
                <!-- Step 2: Select contact or create new -->
                <div class="section hidden" id="contact-section">
                    <h2>Select Contact or Create New</h2>
                    <p>Company: <strong id="selected-company-name"></strong></p>
                    <input type="hidden" id="selected-company-id" />
                    
                    <div id="contact-list" class="contact-list">
                        <div class="loading">Loading contacts...</div>
                    </div>
                    
                    <button onclick="showNewContactForm()">Create New Contact</button>
                    <button class="secondary" onclick="backToSearch()">Back to Search</button>
                </div>
                
                <!-- Form: Add to existing contact -->
                <div class="section hidden" id="add-phone-section">
                    <h2>Add Phone to Contact</h2>
                    <p>Contact: <strong id="contact-name"></strong></p>
                    <input type="hidden" id="contact-id" />
                    
                    <form id="add-phone-form">
                        <div class="form-group">
                            <label>Phone Number</label>
                            <input type="text" name="phone" value="{phone_number}" readonly />
                        </div>
                        <div class="form-group">
                            <label>Phone Type</label>
                            <select name="phone_type">
                                <option value="Cell">Cell</option>
                                <option value="Direct">Direct</option>
                                <option value="Mobile">Mobile</option>
                                <option value="Phone">Phone</option>
                                <option value="Fax">Fax</option>
                            </select>
                        </div>
                        <button type="submit">Add Phone Number</button>
                        <button type="button" class="secondary" onclick="backToContacts()">Back</button>
                    </form>
                </div>
                
                <!-- Form: Create new contact -->
                <div class="section hidden" id="new-contact-section">
                    <h2>Create New Contact</h2>
                    <p>Company: <strong id="new-contact-company-name"></strong></p>
                    
                    <form id="new-contact-form">
                        <input type="hidden" id="new-contact-company-id" />
                        <div class="form-grid">
                            <div class="form-group">
                                <label>First Name *</label>
                                <input type="text" name="first_name" required />
                            </div>
                            <div class="form-group">
                                <label>Last Name *</label>
                                <input type="text" name="last_name" required />
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Phone Number *</label>
                            <input type="text" name="phone" value="{phone_number}" readonly />
                        </div>
                        <div class="form-group">
                            <label>Email</label>
                            <input type="email" name="email" />
                        </div>
                        <div class="form-group">
                            <label>Phone Type</label>
                            <select name="phone_type">
                                <option value="Cell">Cell</option>
                                <option value="Direct">Direct</option>
                                <option value="Mobile">Mobile</option>
                                <option value="Phone">Phone</option>
                            </select>
                        </div>
                        <button type="submit">Create Contact</button>
                        <button type="button" class="secondary" onclick="backToContacts()">Back</button>
                    </form>
                </div>
                
                <!-- Form: Create new company -->
                <div class="section hidden" id="new-company-section">
                    <h2>Create New Company</h2>
                    
                    <form id="new-company-form">
                        <div class="form-group">
                            <label>Company Name *</label>
                            <input type="text" name="name" required />
                        </div>
                        <div class="form-group">
                            <label>Address *</label>
                            <input type="text" name="address" required />
                        </div>
                        <div class="form-group">
                            <label>Address 2</label>
                            <input type="text" name="address2" />
                        </div>
                        <div class="form-grid">
                            <div class="form-group">
                                <label>City *</label>
                                <input type="text" name="city" required />
                            </div>
                            <div class="form-group">
                                <label>State *</label>
                                <input type="text" name="state" required maxlength="2" placeholder="CA" />
                            </div>
                        </div>
                        <div class="form-grid">
                            <div class="form-group">
                                <label>ZIP Code *</label>
                                <input type="text" name="zip_code" required />
                            </div>
                            <div class="form-group">
                                <label>Company Phone *</label>
                                <input type="text" name="company_phone" required />
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Territory</label>
                            <input type="text" name="territory" value="Main" />
                        </div>
                        
                        <h3 style="margin-top: 30px;">Primary Contact</h3>
                        <div class="form-grid">
                            <div class="form-group">
                                <label>First Name *</label>
                                <input type="text" name="first_name" required />
                            </div>
                            <div class="form-group">
                                <label>Last Name *</label>
                                <input type="text" name="last_name" required />
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Email *</label>
                            <input type="email" name="email" required />
                        </div>
                        <div class="form-group">
                            <label>Phone Number *</label>
                            <input type="text" name="phone" value="{phone_number}" readonly />
                        </div>
                        
                        <button type="submit">Create Company & Contact</button>
                        <button type="button" class="secondary" onclick="backToSearch()">Back</button>
                    </form>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
                    <a href="/sync?force=true">
                        Sync Cache Now
                    </a> |
                    <a href="https://app.nilear.com/mtx" target="_blank">
                        Open Nilear
                    </a> |
                    <a href="/">
                        Home
                    </a>
                </div>
            </div>
            
            <script>
                let selectedCompany = null;
                let debounceTimer = null;
                
                // Company search autocomplete
                document.getElementById('company-search').addEventListener('input', function(e) {{
                    clearTimeout(debounceTimer);
                    const query = e.target.value;
                    
                    if (query.length < 2) {{
                        document.getElementById('autocomplete-results').classList.add('hidden');
                        return;
                    }}
                    
                    debounceTimer = setTimeout(async () => {{
                        const response = await fetch(`/api/companies/search?q=${{encodeURIComponent(query)}}`);
                        const companies = await response.json();
                        
                        const resultsDiv = document.getElementById('autocomplete-results');
                        resultsDiv.innerHTML = '';
                        
                        if (companies.length === 0) {{
                            resultsDiv.innerHTML = '<div class="autocomplete-item">No companies found</div>';
                        }} else {{
                            companies.forEach(company => {{
                                const div = document.createElement('div');
                                div.className = 'autocomplete-item';
                                div.textContent = company.label;
                                div.onclick = () => selectCompany(company);
                                resultsDiv.appendChild(div);
                            }});
                        }}
                        
                        resultsDiv.classList.remove('hidden');
                    }}, 300);
                }});
                
                async function selectCompany(company) {{
                    selectedCompany = company;
                    document.getElementById('autocomplete-results').classList.add('hidden');
                    document.getElementById('company-search').value = company.name;
                    
                    // Show contact section
                    document.getElementById('search-section').classList.add('hidden');
                    document.getElementById('contact-section').classList.remove('hidden');
                    document.getElementById('selected-company-name').textContent = company.name;
                    document.getElementById('selected-company-id').value = company.id;
                    
                    // Load contacts
                    const response = await fetch(`/api/companies/${{company.id}}/contacts`);
                    const contacts = await response.json();
                    
                    const contactList = document.getElementById('contact-list');
                    contactList.innerHTML = '';
                    
                    if (contacts.length === 0) {{
                        contactList.innerHTML = '<p style="color: #6b7280;">No contacts found. Create a new one.</p>';
                    }} else {{
                        contacts.forEach(contact => {{
                            const div = document.createElement('div');
                            div.className = 'contact-item';
                            div.innerHTML = `
                                <div>
                                    <strong>${{contact.name}}</strong><br>
                                    <small style="color: #6b7280;">${{contact.phones.join(', ') || 'No phones'}}</small>
                                </div>
                                <button onclick="selectContact(${{contact.id}}, '${{contact.name}}')">
                                    Add Phone to This Contact
                                </button>
                            `;
                            contactList.appendChild(div);
                        }});
                    }}
                }}
                
                function selectContact(contactId, contactName) {{
                    document.getElementById('contact-section').classList.add('hidden');
                    document.getElementById('add-phone-section').classList.remove('hidden');
                    document.getElementById('contact-name').textContent = contactName;
                    document.getElementById('contact-id').value = contactId;
                }}
                
                function showNewContactForm() {{
                    document.getElementById('contact-section').classList.add('hidden');
                    document.getElementById('new-contact-section').classList.remove('hidden');
                    document.getElementById('new-contact-company-name').textContent = selectedCompany.name;
                    document.getElementById('new-contact-company-id').value = selectedCompany.id;
                }}
                
                function showNewCompanyForm() {{
                    document.getElementById('search-section').classList.add('hidden');
                    document.getElementById('new-company-section').classList.remove('hidden');
                }}
                
                function backToSearch() {{
                    document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'));
                    document.getElementById('search-section').classList.remove('hidden');
                }}
                
                function backToContacts() {{
                    document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'));
                    document.getElementById('contact-section').classList.remove('hidden');
                }}
                
                function showMessage(message, isError = false) {{
                    const messageDiv = document.getElementById('message');
                    messageDiv.className = isError ? 'error' : 'success';
                    messageDiv.textContent = message;
                    messageDiv.style.display = 'block';
                    
                    setTimeout(() => {{
                        messageDiv.style.display = 'none';
                    }}, 5000);
                }}
                
                // Form submissions
                document.getElementById('add-phone-form').addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    formData.append('phone', '{phone_number}');
                    
                    const contactId = document.getElementById('contact-id').value;
                    const response = await fetch(`/api/contacts/${{contactId}}/add-phone`, {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showMessage('Phone number added successfully! Redirecting...');
                        setTimeout(() => {{
                            window.location.href = '/company/' + selectedCompany.id;
                        }}, 2000);
                    }} else {{
                        showMessage(result.message, true);
                    }}
                }});
                
                document.getElementById('new-contact-form').addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    formData.append('company_id', document.getElementById('new-contact-company-id').value);
                    formData.append('phone', '{phone_number}');
                    
                    const response = await fetch('/api/contacts/create', {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showMessage('Contact created successfully! Redirecting...');
                        setTimeout(() => {{
                            window.location.href = '/company/' + selectedCompany.id;
                        }}, 2000);
                    }} else {{
                        showMessage(result.message, true);
                    }}
                }});
                
                document.getElementById('new-company-form').addEventListener('submit', async (e) => {{
                    e.preventDefault();
                    const formData = new FormData(e.target);
                    formData.append('phone', '{phone_number}');
                    
                    const response = await fetch('/api/companies/create', {{
                        method: 'POST',
                        body: formData
                    }});
                    
                    const result = await response.json();
                    
                    if (result.success) {{
                        showMessage('Company and contact created successfully! Redirecting...');
                        setTimeout(() => {{
                            window.location.href = '/company/' + result.company_id;
                        }}, 2000);
                    }} else {{
                        showMessage(result.message, true);
                    }}
                }});
            </script>
        </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 3000))
    
    print(f"\n{'='*60}")
    print(f"🚀 8x8 Nilear Screenpop Server v2.1")
    print(f"{'='*60}")
    print(f"Server starting on port {port}")
    print(f"ConnectWise: {CW_BASE_URL}")
    print(f"Nilear: {NILEAR_BASE_URL}")
    print(f"Cache: SQLite (data/phone_cache.db)")
    print(f"Auto-sync: Every {SYNC_INTERVAL_HOURS} hours")
    print(f"\nNew Features:")
    print(f"  • Company search with autocomplete")
    print(f"  • Add phone to existing contacts")
    print(f"  • Create new contacts")
    print(f"  • Create new companies")
    print(f"  • Auto-activate in finance module")
    print(f"\nEndpoints:")
    print(f"  Home:   http://localhost:{port}/")
    print(f"  Health: http://localhost:{port}/health")
    print(f"  Pop:    http://localhost:{port}/screenpop?phone=4084511400")
    print(f"{'='*60}\n")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
