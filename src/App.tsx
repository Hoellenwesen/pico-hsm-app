import { useEffect, useState } from "react";
import { Toaster } from "sonner";
import { ScrollText } from "lucide-react";
import { Sidebar, type TabId } from "./components/layout/Sidebar";
import { PinDialog } from "./components/PinDialog";
import { Certificates } from "./pages/Certificates";
import { Dashboard } from "./pages/Dashboard";
import { DeviceConfig } from "./pages/DeviceConfig";
import { Backup } from "./pages/Backup";
import { Firmware } from "./pages/Firmware";
import { Keys } from "./pages/Keys";
import { DummyPage } from "./pages/DummyPage";
import { useDevice } from "./hooks/useDevice";

export default function App() {
  const [tab, setTab] = useState<TabId>("dashboard");
  const [dark, setDark] = useState(true);
  const device = useDevice();

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar active={tab} onNavigate={setTab} device={device} dark={dark} onToggleTheme={() => setDark((v) => !v)} />
      <main className="min-w-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-6xl">
          {tab === "dashboard" && <Dashboard device={device} onSetup={() => setTab("device")} />}
          {tab === "keys" && <Keys device={device} />}
          {tab === "certs" && <Certificates device={device} />}
          {tab === "backup" && <Backup device={device} />}
          {tab === "device" && <DeviceConfig device={device} />}
          {tab === "firmware" && <Firmware device={device} />}
          {tab === "logs" && (
            <DummyPage title="Logs" subtitle="Device logs / audit logs land here in a later step (design preview)." icon={ScrollText} />
          )}
        </div>
      </main>
      <Toaster richColors position="bottom-right" closeButton />
      <PinDialog device={device} />
    </div>
  );
}
