"""chaster -- Chaster Public API integration package.

CHASTER_USER_AGENT is the explicit, stable User-Agent value every
Chaster HTTP request in this project must send. Chaster's own
developer support confirmed their API/Cloudflare edge rejects
requests carrying Python's default client User-Agent (e.g.
`python-requests/x.y.z`, `Python-urllib/3.x`) -- an explicit,
non-default value is required for a request to succeed at all,
independent of endpoint, method, or auth scheme. Defined once here
so every Chaster HTTP client in this package sends the identical
value; never construct a Chaster request without it.
"""

CHASTER_USER_AGENT = "AI-Coach-Keyholder"
