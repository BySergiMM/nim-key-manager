"""MCP connector layer: exposes the key-manager as a Claude custom connector.

This package is an *inbound adapter* (like ``app.api``): it wraps the existing
application services as Model Context Protocol tools, secured with OAuth 2.1 so
Claude can connect to it as a custom connector. It adds no business logic of its
own — every operation is delegated to the same audited, RBAC-checked services
used by the REST API.
"""
