"use client";

import { useEffect, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { providersApi, type CatalogEntry } from "@/lib/api/providers";
import { usePrompterStore } from "@/store/prompter-store";

/** Fallback models when the API is unavailable */
const FALLBACK_MODELS: CatalogEntry[] = [
  {
    model_name: "claude-haiku-4-5",
    provider_type: "anthropic" as CatalogEntry["provider_type"],
    display_name: "Claude Haiku — Fast & lightweight",
  },
  {
    model_name: "claude-sonnet-4-5",
    provider_type: "anthropic" as CatalogEntry["provider_type"],
    display_name: "Claude Sonnet — Balanced (default)",
  },
  {
    model_name: "claude-opus-4-5",
    provider_type: "anthropic" as CatalogEntry["provider_type"],
    display_name: "Claude Opus — Most capable",
  },
];

export function ModelSelector() {
  const { selectedModel, setSelectedModel } = usePrompterStore();
  const [open, setOpen] = useState(false);
  const [models, setModels] = useState<CatalogEntry[]>(FALLBACK_MODELS);

  useEffect(() => {
    providersApi
      .catalog()
      .then((catalog) => {
        if (catalog.length > 0) {
          setModels(catalog);
          // If current selection not in catalog, reset to first sonnet or first entry
          const inCatalog = catalog.some((m) => m.model_name === selectedModel);
          if (!inCatalog) {
            const sonnet = catalog.find((m) =>
              m.model_name.toLowerCase().includes("sonnet")
            );
            setSelectedModel((sonnet ?? catalog[0]).model_name);
          }
        }
      })
      .catch(() => {
        /* keep fallback models */
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = models.find((m) => m.model_name === selectedModel) ?? models[1];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-[260px] justify-between text-sm"
        >
          <span className="truncate">{current?.display_name ?? selectedModel}</span>
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[320px] p-0">
        <div className="max-h-64 overflow-auto">
          {models.map((model) => (
            <button
              key={model.model_name}
              className={cn(
                "flex w-full items-start gap-2 px-3 py-2 text-sm hover:bg-muted transition-colors text-left",
                model.model_name === selectedModel && "bg-muted"
              )}
              onClick={() => {
                setSelectedModel(model.model_name);
                setOpen(false);
              }}
            >
              <Check
                className={cn(
                  "mt-0.5 h-4 w-4 shrink-0",
                  model.model_name === selectedModel
                    ? "opacity-100"
                    : "opacity-0"
                )}
              />
              <div>
                <div className="font-medium">{model.display_name}</div>
                <div className="text-xs text-muted-foreground capitalize">
                  {model.provider_type}
                </div>
              </div>
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
}
