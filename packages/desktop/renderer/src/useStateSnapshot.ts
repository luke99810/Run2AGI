import { useEffect, useState } from "react";
import type {
  StateSnapshot,
  StateSourceDescriptor,
} from "@desktop/data/state-source";
import { stateSource } from "./state-source";

interface StateSnapshotResult {
  readonly sources: readonly StateSourceDescriptor[];
  readonly selectedSourceId: string;
  readonly selectSource: (sourceId: string) => void;
  readonly snapshot: StateSnapshot | null;
  readonly loading: boolean;
  readonly error: string | null;
}

export function useStateSnapshot(): StateSnapshotResult {
  const [sources, setSources] = useState<readonly StateSourceDescriptor[]>([]);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void stateSource
      .list()
      .then((availableSources) => {
        if (cancelled) return;
        setSources(availableSources);
        const preferred = availableSources.find((source) => source.id === "mid-flight");
        setSelectedSourceId(preferred?.id ?? availableSources[0]?.id ?? "");
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (selectedSourceId.length === 0) {
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    void stateSource
      .read(selectedSourceId)
      .then((nextSnapshot) => {
        if (cancelled) return;
        setSnapshot(nextSnapshot);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedSourceId]);

  return {
    sources,
    selectedSourceId,
    selectSource: setSelectedSourceId,
    snapshot,
    loading,
    error,
  };
}
