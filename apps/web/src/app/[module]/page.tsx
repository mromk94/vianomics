import { notFound } from "next/navigation";

import { ModulePlaceholder } from "@/components/module-placeholder";
import { findModule } from "@/lib/modules";

export default async function ModulePage({
  params,
}: {
  params: Promise<{ module: string }>;
}) {
  const { module: slug } = await params;
  const mod = findModule(slug);
  if (!mod || mod.slug === "") notFound();
  return <ModulePlaceholder slug={slug} />;
}

export async function generateStaticParams() {
  return [];
}
