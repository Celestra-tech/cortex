import type { Transport } from "./http";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PageParams {
  limit?: number;
  offset?: number;
}

export abstract class APIResource {
  constructor(protected readonly transport: Transport) {}

  /** Yields every item across pages, fetching lazily. */
  protected async *paginate<T>(
    fetchPage: (params: Required<PageParams>) => Promise<Page<T>>,
    { limit = 100, offset = 0 }: PageParams = {},
  ): AsyncGenerator<T> {
    for (let cursor = offset; ;) {
      const page = await fetchPage({ limit, offset: cursor });
      yield* page.items;
      cursor += page.items.length;
      if (page.items.length === 0 || cursor >= page.total) return;
    }
  }
}

export function segment(id: string): string {
  return encodeURIComponent(id);
}
