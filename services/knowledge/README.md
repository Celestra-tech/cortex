# cortex-knowledge

Document ingestion, chunking, embedding, indexing, and retrieval that grounds
model responses in organisational knowledge.

**Status:** the implementation ships inside the API at `apps/api/src/cortex_api/services/knowledge/`. This package is the boundary it moves to once it needs to scale or deploy independently of the gateway; see [ARCHITECTURE.md](../../ARCHITECTURE.md).

Package: `cortex_knowledge` · uv workspace member · Python 3.13
