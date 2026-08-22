import { AppShell } from "@/components/app-shell";
import { SellerProfit } from "@/components/seller-profit";

export default async function ProfitModelPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <AppShell current="profit">
      <SellerProfit modelId={id} />
    </AppShell>
  );
}
