import { useConnectionsContext } from "./ConnectionsContext";
import { AIProviderDrawer } from "./AIProviderDrawer";
import { DestinationDrawer } from "./DestinationDrawer";
import { SlackDrawer } from "./SlackDrawer";
import { SourceDrawer } from "./SourceDrawer";

export const ConnectionDrawers = () => {
  const {
    sourceDrawer,
    setSourceDrawer,
    saveSource,
    destinationDrawer,
    setDestinationDrawer,
    sources,
    saveDestination,
    aiProviderDrawer,
    setAiProviderDrawer,
    aiProviderTypes,
    saveAiProvider,
    slackDrawer,
    setSlackDrawer,
    saveSlackConfig
  } = useConnectionsContext();

  return (
    <>
      <SourceDrawer
        key={sourceDrawer && sourceDrawer !== "new" ? sourceDrawer.id : String(sourceDrawer)}
        value={sourceDrawer}
        otherSources={sources.data ?? []}
        saving={saveSource.isPending}
        onClose={() => setSourceDrawer(null)}
        onSave={(body) => saveSource.mutate(body)}
      />
      <DestinationDrawer
        key={destinationDrawer && destinationDrawer !== "new" ? destinationDrawer.id : String(destinationDrawer)}
        value={destinationDrawer}
        sources={sources.data ?? []}
        saving={saveDestination.isPending}
        onClose={() => setDestinationDrawer(null)}
        onSave={(body) => saveDestination.mutate(body)}
      />
      <AIProviderDrawer
        key={aiProviderDrawer && aiProviderDrawer !== "new" ? aiProviderDrawer.id : String(aiProviderDrawer)}
        value={aiProviderDrawer}
        providerTypes={aiProviderTypes.data ?? []}
        saving={saveAiProvider.isPending}
        onClose={() => setAiProviderDrawer(null)}
        onSave={(body) => saveAiProvider.mutate(body)}
      />
      <SlackDrawer
        key={slackDrawer && slackDrawer !== "new" ? slackDrawer.id : String(slackDrawer)}
        value={slackDrawer}
        saving={saveSlackConfig.isPending}
        onClose={() => setSlackDrawer(null)}
        onSave={(body) => saveSlackConfig.mutate(body)}
      />
    </>
  );
};
