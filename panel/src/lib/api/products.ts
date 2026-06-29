import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  Product,
  ProductCreate,
  ProductSummary,
  ProductUpdate,
} from "@/types";

export const productsApi = {
  list: async (): Promise<ProductSummary[]> => {
    if (isMockMode()) return [];
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
