import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  Product,
  ProductCreate,
  ProductSummary,
  ProductUpdate,
} from "@/types";
import { Team } from "@/types";

// Mock products — shapes match the real ProductSummaryResponse (cells + progress).
function mockProducts(): ProductSummary[] {
  return [
    {
      id: "p-mock-1",
      name: "RoboCo Platform",
      slug: "roboco-platform",
      cell_count: 3,
      cells: [
        { team: Team.BACKEND, project_id: "proj-1", project_name: "roboco" },
        { team: Team.FRONTEND, project_id: "proj-1", project_name: "roboco" },
        { team: Team.UX_UI, project_id: "proj-1", project_name: "roboco" },
      ],
      progress: { done: 42, active: 5, blocked: 1 },
    },
    {
      id: "p-mock-2",
      name: "Docs Site",
      slug: "docs-site",
      cell_count: 2,
      cells: [
        { team: Team.FRONTEND, project_id: "proj-2", project_name: "roboco-website" },
        { team: Team.BACKEND, project_id: "proj-3", project_name: "docs-api" },
      ],
      progress: { done: 18, active: 3, blocked: 0 },
    },
  ];
}

export const productsApi = {
  list: async (): Promise<ProductSummary[]> => {
    if (isMockMode()) return mockProducts();
    const { data } = await api.get<ProductSummary[]>("/products");
    return data;
  },
  get: async (id: string): Promise<Product> => {
    const { data } = await api.get<Product>(`/products/${id}`);
    return data;
  },
  create: async (product: ProductCreate): Promise<Product> => {
    const { data } = await api.post<Product>("/products", product);
    return data;
  },
  update: async (id: string, patch: ProductUpdate): Promise<Product> => {
    const { data } = await api.patch<Product>(`/products/${id}`, patch);
    return data;
  },
  delete: async (id: string): Promise<void> => {
    await api.delete(`/products/${id}`);
  },
};
