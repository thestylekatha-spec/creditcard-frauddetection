import AuthGate from "@/components/AuthGate";
import HomeContent from "@/components/HomeContent";

export default function Home() {
  return (
    <AuthGate>
      <HomeContent />
    </AuthGate>
  );
}
