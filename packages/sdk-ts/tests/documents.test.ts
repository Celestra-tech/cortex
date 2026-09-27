import { describe, expect, it } from "vitest";

import { ConflictError, ValidationError } from "../src";
import { cortex, json, mockFetch } from "./helpers";

const DOCUMENT = {
  id: "doc-1",
  title: "Handbook",
  source: "handbook.pdf",
  mime_type: "application/pdf",
  metadata: { team: "ops" },
  content_hash: "abc",
  byte_size: 2048,
  char_count: 900,
  token_count: 210,
  chunk_count: 3,
  embedding_space: "local/hashing@256",
  ingestion: { ingestion_ms: 40, embedding_ms: 5, stages: {} },
  created_at: "2026-09-27T12:00:00+00:00",
};

describe("documents.upload", () => {
  it("sends a multipart form with the file and options", async () => {
    const { fetch, calls } = mockFetch(json(DOCUMENT, 201));
    const pdf = new Blob([new Uint8Array([37, 80, 68, 70])], { type: "application/pdf" });

    const document = await cortex(fetch).documents.upload(pdf, {
      filename: "handbook.pdf",
      title: "Handbook",
      metadata: { team: "ops" },
      chunking: { chunk_size: 256, separators: ["\n\n"] },
    });

    expect(document.chunk_count).toBe(3);
    const call = calls[0]!;
    expect(call.url.pathname).toBe("/v1/documents/ingest");
    expect(call.headers.has("content-type")).toBe(false);
    const form = call.body as FormData;
    const file = form.get("file") as File;
    expect(file.name).toBe("handbook.pdf");
    expect(file.size).toBe(4);
    expect(form.get("title")).toBe("Handbook");
    expect(form.get("metadata")).toBe('{"team":"ops"}');
    expect(form.get("chunk_size")).toBe("256");
    expect(form.get("separators")).toBe('["\\n\\n"]');
    expect(form.has("chunk_overlap")).toBe(false);
  });

  it("uses a File's own name and accepts raw bytes", async () => {
    const { fetch, calls } = mockFetch(json(DOCUMENT, 201), json(DOCUMENT, 201));
    const client = cortex(fetch);
    await client.documents.upload(new File(["# Notes"], "notes.md"));
    await client.documents.upload(new TextEncoder().encode("plain"), { filename: "a.txt" });
    expect(((calls[0]!.body as FormData).get("file") as File).name).toBe("notes.md");
    expect(((calls[1]!.body as FormData).get("file") as File).name).toBe("a.txt");
  });

  it("requires a filename for anonymous data", async () => {
    const { fetch, calls } = mockFetch();
    await expect(cortex(fetch).documents.upload(new Blob(["x"]))).rejects.toBeInstanceOf(
      ValidationError,
    );
    expect(calls).toHaveLength(0);
  });

  it("reports duplicates with the existing document", async () => {
    const { fetch } = mockFetch(
      json({ detail: "Identical content already ingested", document_id: "doc-1" }, 409),
    );
    const error = (await cortex(fetch)
      .documents.upload(new File(["same"], "a.txt"))
      .catch((e: unknown) => e)) as ConflictError;
    expect(error).toBeInstanceOf(ConflictError);
    expect(error.documentId).toBe("doc-1");
  });

  it("allows longer uploads by default", async () => {
    const { fetch, calls } = mockFetch(json(DOCUMENT, 201));
    await cortex(fetch, { timeoutMs: 5 }).documents.upload(new File(["x"], "a.txt"));
    expect(calls).toHaveLength(1);
  });
});

describe("documents", () => {
  it("creates text documents, lists, reads, and deletes", async () => {
    const { fetch, calls } = mockFetch(
      json(DOCUMENT, 201),
      json({ items: [DOCUMENT], total: 1, limit: 50, offset: 0 }),
      json({ ...DOCUMENT, chunks: [] }),
      new Response(null, { status: 204 }),
    );
    const client = cortex(fetch);
    await client.documents.create({
      title: "Notes",
      content: "# Notes",
      mime_type: "text/markdown",
    });
    await client.documents.list({ mimeType: "application/pdf" });
    await client.documents.get("doc-1", { includeChunks: true, chunkLimit: 5 });
    await client.documents.delete("doc-1");

    expect(calls.map((c) => `${c.method} ${c.url.pathname}${c.url.search}`)).toEqual([
      "POST /v1/documents",
      "GET /v1/documents?mime_type=application%2Fpdf",
      "GET /v1/documents/doc-1?include_chunks=true&chunk_limit=5",
      "DELETE /v1/documents/doc-1",
    ]);
  });
});
