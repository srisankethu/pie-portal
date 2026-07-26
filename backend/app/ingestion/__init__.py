"""Zoho ingestion boundary: pull raw ERP payloads, adapt them into validated
canonical DTOs, and upsert the read model. This is the ONLY place raw Zoho
structures are allowed; nothing downstream sees a Zoho-shaped dict.
"""
