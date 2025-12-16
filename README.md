# 8x8 Work + ConnectWise + Nilear Screenpop v2.1

Python FastAPI middleware with **SQLite caching** and **complete contact management** that enables automatic screenpops in 8x8 Work by searching ConnectWise for incoming phone numbers and redirecting to Nilear client pages.

## ✨ New in v2.1

- 🔍 **Company search with autocomplete** - Find existing companies as you type
- 👤 **Add phone to existing contacts** - Assign number to current contact
- ✨ **Create new contacts** - Add new person to existing company
- 🏢 **Create new companies** - Full company creation with primary contact
- 💰 **Auto-activate finance** - Automatically enable billing for new companies
- 📝 **Interactive workflow** - Step-by-step UI for handling unknown numbers

## 🎯 Complete Workflow

### When Contact Found (Cached)
```
Call → Check Cache → Found → Redirect to Nilear
```

### When Contact NOT Found
```
Call → Check Cache → Not Found → Show Interactive Page:

1. Search for Company (autocomplete)
   ├─ Company Found → List Contacts
   │                  ├─ Select Contact → Add Phone
   │                  └─ Create New Contact
   └─ Company Not Found → Create New Company Form
                          └─ Creates Company + Contact + Activates Finance
```

## 📋 Prerequisites

- Python 3.8 or higher
- ConnectWise Manage API credentials
- 8x8 Work phone system
- A publicly accessible server or ngrok for testing

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

The `.env` file contains your configuration:

```env
# ConnectWise API Credentials
CW_CLIENT_ID=your-client-id
CW_PUBLIC_API_KEY=your-public-key
CW_PRIVATE_API_KEY=your-private-key
CW_COMPANY_ID=your-company-id
CW_BASE_URL=https://your-instance.com/v4_6_release/apis/3.0

# Nilear Configuration
NILEAR_BASE_URL=https://mtx.link

# Cache Configuration
SYNC_INTERVAL_HOURS=4

# Server Configuration
PORT=3000
```

### 3. Start the Server

```bash
python server.py
```

On first startup, the server will automatically sync all phone numbers from ConnectWise.

### 4. Configure 8x8 Work

1. Open 8x8 Work settings
2. Go to **Integrations** → **Screen Pop**
3. Set URL to:
   ```
   https://your-server.com/screenpop?phone=%%CallerNumber%%
   ```

## 🆕 Contact Management Features

### Company Search

When a phone number isn't found in the cache, users see an interactive page with:

**Search Box with Autocomplete:**
- Start typing company name
- Real-time search results
- Shows company name, city, state
- Click to select

### Adding to Existing Contact

**After selecting a company:**
1. View all contacts at that company
2. See existing phone numbers for each contact
3. Click "Add Phone to This Contact"
4. Select phone type (Cell, Direct, Mobile, etc.)
5. Submit → Phone added + Cache updated + Redirect to Nilear

### Creating New Contact

**If contact doesn't exist:**
1. Click "Create New Contact"
2. Fill in form:
   - First Name (required)
   - Last Name (required)
   - Email (optional)
   - Phone Type (Cell/Direct/Mobile/Phone)
   - Phone Number (pre-filled)
3. Submit → Contact created + Cache updated + Redirect to Nilear

### Creating New Company

**If company doesn't exist:**
1. Click "Create New Company"
2. Fill in comprehensive form:
   
   **Company Information:**
   - Company Name (required)
   - Address (required)
   - Address 2 (optional)
   - City (required)
   - State (required, 2 letters)
   - ZIP Code (required)
   - Company Phone (required)
   - Territory (default: "Main")
   
   **Primary Contact:**
   - First Name (required)
   - Last Name (required)
   - Email (required)
   - Phone Number (pre-filled from caller)

3. Submit → Company created + Contact created + Finance activated + Cache updated + Redirect to Nilear

## 📡 New API Endpoints

### GET /api/companies/search?q=acme

Search for companies with autocomplete.

**Query Parameters:**
- `q` (required): Search query (min 2 characters)

**Response:**
```json
[
  {
    "id": 250,
    "name": "ACME Corporation",
    "identifier": "ACME",
    "city": "San Jose",
    "state": "CA",
    "phone": "4084511400",
    "label": "ACME Corporation - San Jose, CA"
  }
]
```

### GET /api/companies/{company_id}/contacts

Get all contacts for a company.

**Response:**
```json
[
  {
    "id": 123,
    "name": "John Doe",
    "firstName": "John",
    "lastName": "Doe",
    "phones": ["4081234567", "4089876543"]
  }
]
```

### POST /api/contacts/{contact_id}/add-phone

Add a phone number to an existing contact.

**Form Data:**
- `phone`: Phone number to add
- `phone_type`: Type (Cell, Direct, Mobile, Phone, Fax)

**Response:**
```json
{
  "success": true,
  "message": "Phone 4084511400 added to contact"
}
```

### POST /api/contacts/create

Create a new contact with phone number.

**Form Data:**
- `company_id`: Company ID (required)
- `first_name`: First name (required)
- `last_name`: Last name (required)
- `phone`: Phone number (required)
- `email`: Email (optional)
- `phone_type`: Phone type (default: Cell)

**Response:**
```json
{
  "success": true,
  "contact_id": 456,
  "message": "Contact created successfully"
}
```

### POST /api/companies/create

Create a new company with primary contact.

**Form Data:**
- Company: `name`, `address`, `address2`, `city`, `state`, `zip_code`, `company_phone`, `territory`
- Contact: `first_name`, `last_name`, `email`, `phone`

**Response:**
```json
{
  "success": true,
  "company_id": 789,
  "contact_id": 456,
  "message": "Company and contact created successfully"
}
```

## 🗄️ Database & Caching

### Cache Behavior with New Contacts

When you add a phone or create a contact through the UI:
1. Operation completes in ConnectWise
2. Cache immediately updated (no need to wait for sync)
3. User redirected to Nilear
4. Next call from that number → instant screenpop

### Cache Auto-Sync

- Runs every N hours (configurable)
- Picks up any changes made directly in ConnectWise
- Handles contacts/phones added by other users
- Maintains cache freshness

## 🎨 Interactive UI Features

### Real-Time Autocomplete

- Searches as you type (300ms debounce)
- Shows up to 10 results
- Highlights matches
- Keyboard navigation (coming soon)

### Progressive Disclosure

- Only shows relevant sections
- Step-by-step workflow
- Back buttons at each step
- Clear visual hierarchy

### Form Validation

- Required field indicators
- Client-side validation
- Server-side validation
- Clear error messages

### Success Feedback

- Green success messages
- 2-second delay before redirect
- Loading states during API calls
- Graceful error handling

## 🔧 Troubleshooting

### "Company Not Found" in Autocomplete

**Possible Causes:**
- Company name doesn't match search
- Special characters in company name
- Company not in ConnectWise

**Solutions:**
1. Try different search terms
2. Search by city or identifier
3. Click "Create New Company"

### "Failed to Create Company"

**Check These:**
- All required fields filled
- State is 2 letters (CA, not California)
- ZIP code format is valid
- Company name is unique
- Territory name exists in ConnectWise

**Common Issues:**
- Duplicate identifier → System auto-generates
- Invalid territory → Use "Main" as default
- Missing required fields → Check form validation

### Phone Not Added to Contact

**Possible Causes:**
- Phone already exists on contact
- Invalid phone format
- Contact doesn't exist (deleted in meantime)

**Debug Steps:**
```bash
# Check if phone was cached
sqlite3 data/phone_cache.db "SELECT * FROM phone_cache WHERE normalized_phone='4084511400'"

# Check server logs
tail -f /var/log/screenpop.log  # or docker logs
```

### Finance Activation Failed

**Note:** This is a warning, not an error. Company is still created.

**To manually activate:**
1. Log into ConnectWise
2. Go to Company → Finance
3. Add finance record for company
4. Or wait for next cache sync (picks up automatically)

## 🌐 Deployment

### Option 1: Docker (Recommended)

```bash
docker-compose up -d
```

**Features:**
- Persistent database volume
- Auto-restart on failure
- Easy backup/restore
- Isolated environment

### Option 2: systemd Service

See main README for systemd setup.

**Important for Contact Creation:**
Ensure the service has write access to ConnectWise API and can modify the database.

## 📊 Monitoring

### Check Contact Creation Success

```bash
# View recent additions to cache
sqlite3 data/phone_cache.db "
SELECT phone_number, company_name, contact_name, created_at 
FROM phone_cache 
ORDER BY created_at DESC 
LIMIT 10
"
```

### Monitor API Calls

```bash
# Follow server logs
docker-compose logs -f | grep -E "(Creating|Created|Failed)"
```

### Track User Activity

Server logs include:
- Phone searches
- Company searches
- Contact creations
- Company creations
- Finance activations

## 🔒 Security Considerations

### API Access

The contact creation endpoints don't require authentication by default. For production:

```python
# Add API key validation
from fastapi import Header, HTTPException

API_KEY = os.getenv("API_KEY")

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API Key")

@app.post("/api/companies/create", dependencies=[Depends(verify_api_key)])
async def api_create_company(...):
    ...
```

### Input Validation

All forms validate:
- Required fields present
- Phone numbers are numeric
- Email format is valid
- State codes are 2 letters
- ZIP codes match pattern

### SQL Injection

Protected by:
- SQLite parameterized queries
- FastAPI form validation
- Python type hints

## 📁 Project Structure

```
.
├── server.py              # Main application (v2.1)
├── database.py            # SQLite cache manager
├── connectwise_api.py     # ConnectWise API helpers (NEW)
├── requirements.txt       # Python dependencies
├── test_connectwise.py    # API test script
├── data/
│   └── phone_cache.db    # SQLite database
├── .env                   # Environment variables
├── README.md             # This file
├── Dockerfile            # Container definition
└── docker-compose.yml    # Docker with persistent volume
```

## 🆙 Upgrading from v2.0

If upgrading from the cached version without contact creation:

1. **Pull new files:**
   ```bash
   git pull
   # or download: server.py, connectwise_api.py
   ```

2. **No database migration needed** - schema unchanged

3. **Restart server:**
   ```bash
   docker-compose down
   docker-compose up -d
   ```

4. **Test contact creation:**
   - Call from unknown number
   - Verify interactive page appears
   - Try creating test contact

## 🤝 Support

### Common Questions

**Q: Can I disable contact creation and just use cache?**
A: Yes, the screenpop still works the same. Contact creation only appears when number not found.

**Q: What happens if I create a duplicate company?**
A: ConnectWise will reject it. Try searching more carefully or check for typos.

**Q: Can users edit contacts through this interface?**
A: No, this is designed for phone number assignment only. Use ConnectWise for full contact management.

**Q: Does this work with multiple territories?**
A: Yes, specify the territory in the company creation form.

**Q: What if finance activation fails?**
A: Company is still created. You can manually activate in ConnectWise or it syncs automatically.

## 📄 License

MIT License - Created for CNS4U Inc

---

**Made with ❤️ for seamless customer interactions and comprehensive contact management**
