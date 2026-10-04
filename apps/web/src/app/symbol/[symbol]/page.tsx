"use client";

import { use } from "react";

import { SymbolPage } from "@/components/symbol-page";

export default function Page(
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = use(params);
  return <SymbolPage symbol={decodeURIComponent(symbol)} />;
}
