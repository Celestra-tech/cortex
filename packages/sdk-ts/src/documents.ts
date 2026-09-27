import type {
  ChunkingOptions,
  DocumentCreate,
  DocumentDetail,
  DocumentListResponse,
  KnowledgeDocument,
} from "@celestra/cortex-types";

import { ValidationError } from "./errors";
import type { RequestOptions } from "./http";
import { APIResource, type PageParams, segment } from "./resource";
import {
  documentCreateSchema,
  documentListSchema,
  ingestOptionsSchema,
  knowledgeDocumentSchema,
  validateInput,
} from "./types";

/** Ingestion parses, chunks, and embeds; large PDFs take a while. */
const UPLOAD_TIMEOUT_MS = 120_000;

export type Uploadable = Blob | ArrayBuffer | Uint8Array;

export interface UploadParams {
  /** Required unless `file` is a `File`, whose name is used. Drives format detection. */
  filename?: string;
  /** Defaults to the document's own title, then the file name. */
  title?: string;
  /** Defaults to the file name. */
  source?: string;
  metadata?: Record<string, unknown>;
  chunking?: ChunkingOptions;
}

export interface DocumentListParams extends PageParams {
  source?: string;
  mimeType?: string;
}

export interface DocumentGetParams {
  includeChunks?: boolean;
  chunkLimit?: number;
  chunkOffset?: number;
}

/**
 * The knowledge corpus. Uploading identical content twice throws
 * `ConflictError`, whose `documentId` is the existing copy.
 */
export class Documents extends APIResource {
  /**
   * Uploads a PDF, DOCX, Markdown, or text file through the ingestion
   * pipeline. In Node: `await fs.openAsBlob(path)` produces a `Blob`.
   */
  async upload(
    file: Uploadable,
    params: UploadParams = {},
    options: RequestOptions = {},
  ): Promise<KnowledgeDocument> {
    validateInput("documents.upload", ingestOptionsSchema, params);
    const filename = params.filename ?? (isFile(file) ? file.name : undefined);
    if (!filename) {
      throw new ValidationError("documents.upload: `filename` is required unless file is a File", {
        status: null,
        issues: [{ path: "filename", message: "Required" }],
      });
    }
    const blob = file instanceof Blob ? file : new Blob([file as BlobPart]);
    const form = new FormData();
    form.append("file", blob, filename);
    if (params.title !== undefined) form.append("title", params.title);
    if (params.source !== undefined) form.append("source", params.source);
    if (params.metadata !== undefined) form.append("metadata", JSON.stringify(params.metadata));
    const { chunk_size, chunk_overlap, separators } = params.chunking ?? {};
    if (chunk_size !== undefined) form.append("chunk_size", String(chunk_size));
    if (chunk_overlap !== undefined) form.append("chunk_overlap", String(chunk_overlap));
    if (separators !== undefined) form.append("separators", JSON.stringify(separators));

    return this.transport.request({
      operation: "documents.upload",
      method: "POST",
      path: "/v1/documents/ingest",
      form,
      schema: knowledgeDocumentSchema,
      options: { timeoutMs: UPLOAD_TIMEOUT_MS, ...options },
    });
  }

  /** Ingests inline text or Markdown. */
  async create(params: DocumentCreate, options: RequestOptions = {}): Promise<KnowledgeDocument> {
    validateInput("documents.create", documentCreateSchema, params);
    return this.transport.request({
      operation: "documents.create",
      method: "POST",
      path: "/v1/documents",
      json: params,
      schema: knowledgeDocumentSchema,
      options: { timeoutMs: UPLOAD_TIMEOUT_MS, ...options },
    });
  }

  list(params: DocumentListParams = {}, options?: RequestOptions): Promise<DocumentListResponse> {
    return this.transport.request({
      operation: "documents.list",
      path: "/v1/documents",
      query: {
        source: params.source,
        mime_type: params.mimeType,
        limit: params.limit,
        offset: params.offset,
      },
      schema: documentListSchema,
      options,
    });
  }

  /** Every document, fetched page by page. */
  iter(params: DocumentListParams = {}): AsyncGenerator<KnowledgeDocument> {
    return this.paginate((page) => this.list({ ...params, ...page }), params);
  }

  get(
    id: string,
    params: DocumentGetParams = {},
    options?: RequestOptions,
  ): Promise<DocumentDetail> {
    return this.transport.request({
      operation: "documents.get",
      path: `/v1/documents/${segment(id)}`,
      query: {
        include_chunks: params.includeChunks,
        chunk_limit: params.chunkLimit,
        chunk_offset: params.chunkOffset,
      },
      options,
    });
  }

  /** Permanently removes the document, its chunks, and their embeddings. */
  async delete(id: string, options?: RequestOptions): Promise<void> {
    await this.transport.request({
      operation: "documents.delete",
      method: "DELETE",
      path: `/v1/documents/${segment(id)}`,
      options,
    });
  }
}

function isFile(value: unknown): value is File {
  return typeof File !== "undefined" && value instanceof File;
}
