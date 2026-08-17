"""The multi-ERP connector layer: one registry, five US-market systems.

Everything a connector *is* lives in its own module — its auth, its API
shape, its translator onto the canonical payload shape ``normalize`` reads —
declared to the rest of the platform through one :class:`~.base.ConnectorSpec`
in the registry. Generic code (the connect endpoints, the connect form, the
source dispatch in ``ingestion.sync.get_source``) reads the registry and
never branches on a connector key.

Importing this package imports every connector module for the registration
side effect — the same pattern ``alembic/env.py`` uses to find the models.
A module not imported here is a connector that does not exist.
"""
from __future__ import annotations

from . import acumatica, dynamics365, netsuite, prophet21, sage  # noqa: F401
from .base import (CredentialMaterial, ConnectorSpec, Field,  # noqa: F401
                   UnknownConnectorError, catalog, get_spec,
                   split_credential_inputs, split_inputs)
