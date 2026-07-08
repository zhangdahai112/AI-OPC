import { useAppState } from "./context";
import Layout from "./components/Layout";
import ChannelView from "./components/ChannelView";
import ProjectsView from "./components/ProjectsView";
import MetricsView from "./components/MetricsView";
import ConfigView from "./components/ConfigView";
import MarketplaceView from "./components/MarketplaceView";
import NewChannelModal from "./components/NewChannelModal";
import PrivateChatView from "./components/PrivateChatView";
import { useState, useEffect } from "react";

export default function App() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { view, privateChat } = useAppState() as any;
  const [showNewChannel, setShowNewChannel] = useState(false);

  useEffect(() => {
    const handler = () => setShowNewChannel(true);
    document.addEventListener("open-new-channel", handler);
    return () => document.removeEventListener("open-new-channel", handler);
  }, []);

  const renderCenter = () => {
    switch (view) {
      case "channels": return <ChannelView />;
      case "projects": return <ProjectsView />;
      case "metrics": return <MetricsView />;
      case "config": return <ConfigView />;
      case "market": return <MarketplaceView />;
      default: return null;
    }
  };

  return (
    <>
      <Layout center={renderCenter()} />
      {showNewChannel && (
        <NewChannelModal onClose={() => setShowNewChannel(false)} />
      )}
      {privateChat && (
        <div className="pchat-panel">
          <PrivateChatView />
        </div>
      )}
    </>
  );
}
