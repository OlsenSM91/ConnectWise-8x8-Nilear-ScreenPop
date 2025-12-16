"""
ConnectWise API helper functions for company and contact management
"""

import os
import base64
import re
from typing import Optional, List, Dict, Tuple
import httpx
from dotenv import load_dotenv

load_dotenv()

CW_CLIENT_ID = os.getenv("CW_CLIENT_ID")
CW_PUBLIC_KEY = os.getenv("CW_PUBLIC_API_KEY")
CW_PRIVATE_KEY = os.getenv("CW_PRIVATE_API_KEY")
CW_COMPANY_ID = os.getenv("CW_COMPANY_ID")
CW_BASE_URL = os.getenv("CW_BASE_URL")


def get_cw_headers():
    """Generate ConnectWise API headers with authentication"""
    auth_string = f"{CW_COMPANY_ID}+{CW_PUBLIC_KEY}:{CW_PRIVATE_KEY}"
    auth_encoded = base64.b64encode(auth_string.encode()).decode()
    
    return {
        "Authorization": f"Basic {auth_encoded}",
        "ClientId": CW_CLIENT_ID,
        "Content-Type": "application/json"
    }


def generate_company_identifier(company_name: str, max_length=30) -> str:
    """Generate a valid ConnectWise identifier from company name"""
    identifier = re.sub(r"[^a-zA-Z0-9]", "", company_name)[:max_length]
    return identifier or "TempCo"


async def search_companies(query: str, limit: int = 10) -> List[Dict]:
    """
    Search for companies by name in ConnectWise
    Returns list of matching companies
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{CW_BASE_URL}/company/companies"
        params = {
            "conditions": f"name like '%{query}%'",
            "pageSize": limit,
            "orderBy": "name asc",
            "fields": "id,name,identifier,city,state,phoneNumber,status"
        }
        
        response = await client.get(
            url,
            headers=get_cw_headers(),
            params=params
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error searching companies: {response.status_code} - {response.text}")
            return []


async def get_company_by_id(company_id: int) -> Optional[Dict]:
    """Get full company details by ID"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{CW_BASE_URL}/company/companies/{company_id}"
        
        response = await client.get(
            url,
            headers=get_cw_headers()
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            return None


async def get_company_contacts(company_id: int) -> List[Dict]:
    """Get all contacts for a company"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{CW_BASE_URL}/company/contacts"
        params = {
            "conditions": f"company/id={company_id}",
            "pageSize": 100,
            "orderBy": "lastName asc",
            "fields": "id,firstName,lastName,communicationItems"
        }
        
        response = await client.get(
            url,
            headers=get_cw_headers(),
            params=params
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error getting contacts: {response.status_code} - {response.text}")
            return []


async def add_phone_to_contact(contact_id: int, phone_number: str, phone_type: str = "Cell") -> bool:
    """
    Add a phone number to an existing contact
    phone_type: "Cell", "Direct", "Mobile", "Fax", "Phone"
    """
    # Map phone types to ConnectWise communication type IDs
    type_map = {
        "Cell": 2,
        "Mobile": 2,
        "Direct": 3,
        "Phone": 4,
        "Fax": 5
    }
    
    type_id = type_map.get(phone_type, 2)  # Default to Cell
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # First, get current contact to get existing communication items
        contact_url = f"{CW_BASE_URL}/company/contacts/{contact_id}"
        contact_resp = await client.get(contact_url, headers=get_cw_headers())
        
        if contact_resp.status_code != 200:
            print(f"Error getting contact: {contact_resp.text}")
            return False
        
        contact = contact_resp.json()
        comm_items = contact.get("communicationItems", [])
        
        # Check if phone already exists
        for item in comm_items:
            if item.get("value") == phone_number:
                print(f"Phone {phone_number} already exists for contact")
                return True
        
        # Add new communication item
        new_item = {
            "type": {"id": type_id},
            "value": phone_number,
            "communicationType": "Phone"
        }
        comm_items.append(new_item)

        # Update contact using JSON Patch format (RFC 6902)
        patch_operations = [
            {
                "op": "replace",
                "path": "communicationItems",
                "value": comm_items
            }
        ]

        update_resp = await client.patch(
            contact_url,
            headers=get_cw_headers(),
            json=patch_operations
        )
        
        if update_resp.status_code == 200:
            print(f"Added phone {phone_number} to contact {contact_id}")
            return True
        else:
            print(f"Error adding phone: {update_resp.text}")
            return False


async def create_contact_with_phone(
    company_id: int,
    first_name: str,
    last_name: str,
    phone_number: str,
    email: Optional[str] = None,
    phone_type: str = "Cell"
) -> Optional[int]:
    """
    Create a new contact with phone number
    Returns contact_id if successful
    """
    type_map = {
        "Cell": 2,
        "Mobile": 2,
        "Direct": 3,
        "Phone": 4,
        "Fax": 5
    }
    
    phone_type_id = type_map.get(phone_type, 2)
    
    contact_data = {
        "firstName": first_name,
        "lastName": last_name,
        "company": {"id": company_id},
        "communicationItems": [
            {
                "type": {"id": phone_type_id},
                "value": phone_number,
                "communicationType": "Phone"
            }
        ]
    }
    
    # Add email if provided
    if email:
        contact_data["communicationItems"].append({
            "type": {"id": 1},  # Email type
            "value": email,
            "communicationType": "Email"
        })
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{CW_BASE_URL}/company/contacts"
        
        response = await client.post(
            url,
            headers=get_cw_headers(),
            json=contact_data
        )
        
        if response.status_code == 201:
            created_contact = response.json()
            contact_id = created_contact["id"]
            print(f"Created contact with ID: {contact_id}")
            return contact_id
        else:
            print(f"Error creating contact: {response.text}")
            return None


async def create_company_and_contact(
    name: str,
    address: str,
    address2: str,
    city: str,
    state: str,
    zip_code: str,
    company_phone: str,
    territory: str,
    first_name: str,
    last_name: str,
    email: str,
    phone: str
) -> Tuple[Optional[int], Optional[int]]:
    """
    Create a company and contact in ConnectWise
    Returns (company_id, contact_id) or (None, None) on failure
    """
    identifier = generate_company_identifier(name)
    print(f"[DEBUG] Creating company: {name} with identifier: {identifier}")
    
    try:
        company_data = {
            "identifier": identifier,
            "name": name,
            "addressLine1": address,
            "addressLine2": address2,
            "city": city,
            "state": state,
            "zip": zip_code,
            "phoneNumber": company_phone,
            "territory": {"name": territory},
            "site": {"name": "Main Office"},
            "types": [{"id": 1}]  # Client type - required!
        }
        
        print(f"[DEBUG] Company data: {company_data}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Create the company
            company_resp = await client.post(
                f"{CW_BASE_URL}/company/companies",
                headers=get_cw_headers(),
                json=company_data
            )
            
            print(f"[DEBUG] Company creation response: {company_resp.status_code} - {company_resp.text}")
            
            if company_resp.status_code != 201:
                raise Exception(f"Failed to create company: {company_resp.text}")
            
            created_company = company_resp.json()
            company_id = created_company['id']
            print(f"[DEBUG] Company created with ID: {company_id}")
            
            # Activate in finance
            try:
                await activate_company_finance(company_id)
            except Exception as finance_error:
                print(f"[WARNING] Failed to activate company finance: {str(finance_error)}")
                # Continue - company still created
            
            # Create the contact
            contact_data = {
                "firstName": first_name,
                "lastName": last_name,
                "company": {"id": company_id},
                "communicationItems": [
                    {
                        "type": {"id": 1},  # Email
                        "value": email,
                        "communicationType": "Email"
                    },
                    {
                        "type": {"id": 2},  # Mobile/Cell
                        "value": phone,
                        "communicationType": "Phone"
                    }
                ]
            }
            
            contact_resp = await client.post(
                f"{CW_BASE_URL}/company/contacts",
                headers=get_cw_headers(),
                json=contact_data
            )
            
            print(f"[DEBUG] Contact creation response: {contact_resp.status_code} - {contact_resp.text}")
            
            if contact_resp.status_code != 201:
                raise Exception(f"Failed to create contact: {contact_resp.text}")
            
            created_contact = contact_resp.json()
            contact_id = created_contact['id']
            print(f"[DEBUG] Contact created with ID: {contact_id}")
            
            # Update company with default contact
            try:
                # Use JSON Patch format (RFC 6902)
                patch_operations = [
                    {
                        "op": "replace",
                        "path": "defaultContact",
                        "value": {"id": contact_id}
                    }
                ]

                update_resp = await client.patch(
                    f"{CW_BASE_URL}/company/companies/{company_id}",
                    headers=get_cw_headers(),
                    json=patch_operations
                )
                
                print(f"[DEBUG] Company update response: {update_resp.status_code}")
            except Exception as update_error:
                print(f"[WARNING] Failed to update company with default contact: {str(update_error)}")
            
            return company_id, contact_id
            
    except Exception as e:
        print(f"[ERROR] Exception in create_company_and_contact: {str(e)}")
        return None, None


async def get_company_tickets(company_id: int, status_filter: str = "open", limit: int = 25) -> List[Dict]:
    """
    Get tickets for a company from ConnectWise
    status_filter: "open", "all", or specific status
    Returns list of tickets with id, summary, status, priority, etc.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{CW_BASE_URL}/service/tickets"

        # Build conditions based on status filter
        if status_filter == "open":
            conditions = f"company/id={company_id} AND closedFlag=false"
        elif status_filter == "all":
            conditions = f"company/id={company_id}"
        else:
            conditions = f"company/id={company_id} AND status/name='{status_filter}'"

        params = {
            "conditions": conditions,
            "pageSize": limit,
            "orderBy": "id desc",
            "fields": "id,summary,status,priority,board,contact,dateEntered,resources,closedFlag"
        }

        response = await client.get(
            url,
            headers=get_cw_headers(),
            params=params
        )

        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error getting tickets: {response.status_code} - {response.text}")
            return []


async def create_ticket(
    company_id: int,
    contact_id: int,
    summary: str,
    description: str,
    board_name: str,
    priority_name: str = "Priority 3 - Normal Response",
    status_name: str = "New (email)"
) -> Optional[int]:
    """
    Create a service ticket in ConnectWise
    Returns ticket_id if successful, None on failure
    Note: Type is not specified - ConnectWise will use the board's default type
    """
    try:
        ticket_data = {
            "summary": summary,
            "board": {"name": board_name},
            "company": {"id": company_id},
            "contact": {"id": contact_id},
            "priority": {"name": priority_name},
            "status": {"name": status_name},
            "initialDescription": description
        }

        print(f"[DEBUG] Creating ticket with data: {ticket_data}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{CW_BASE_URL}/service/tickets"

            response = await client.post(
                url,
                headers=get_cw_headers(),
                json=ticket_data
            )

            if response.status_code == 201:
                created_ticket = response.json()
                ticket_id = created_ticket["id"]
                print(f"✓ Created ticket #{ticket_id}: {summary}")
                return ticket_id
            else:
                print(f"[TICKET ERROR] {response.status_code}")
                try:
                    error_json = response.json()
                    print(f"[TICKET ERROR MESSAGE]: {error_json.get('message', 'No message')}")
                    if "errors" in error_json:
                        for err in error_json["errors"]:
                            print(f"[TICKET ERROR DETAIL]: {err.get('message', '')}")
                except Exception:
                    print(f"[TICKET ERROR RAW TEXT]: {response.text}")
                return None

    except Exception as e:
        print(f"[EXCEPTION] Error creating ticket: {str(e)}")
        return None


async def activate_company_finance(company_id: int) -> bool:
    """
    Activate a company in the finance module
    This is required for billing/invoicing
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # First check if finance record exists
            check_url = f"{CW_BASE_URL}/finance/companyFinance"
            check_params = {
                "conditions": f"company/id={company_id}",
                "pageSize": 1
            }

            check_resp = await client.get(
                check_url,
                headers=get_cw_headers(),
                params=check_params
            )

            if check_resp.status_code == 200 and check_resp.json():
                print(f"Finance record already exists for company {company_id}")
                return True

            # Create finance record
            finance_data = {
                "company": {"id": company_id},
                "accountNumber": f"C{company_id:06d}",  # Format: C000250
                "billingTerms": {"id": 2},  # Net 15 days (adjust as needed)
                "taxCode": {"id": 1},  # Default tax code (adjust as needed)
            }

            create_url = f"{CW_BASE_URL}/finance/companyFinance"
            create_resp = await client.post(
                create_url,
                headers=get_cw_headers(),
                json=finance_data
            )

            if create_resp.status_code == 201:
                print(f"Activated company {company_id} in finance module")
                return True
            else:
                print(f"Error activating finance: {create_resp.status_code} - {create_resp.text}")
                return False

    except Exception as e:
        print(f"Error activating company finance: {str(e)}")
        return False
