import { useEffect, useState } from "react";

import { api, IS_MOCK_MODE } from "../api/client";
import type { CapabilityDescriptor, JobType } from "../api/types";

interface CapabilityAvailability {
  available: boolean;
  loading: boolean;
  reason?: string;
}

export function useCapabilityAvailability(jobType: JobType): CapabilityAvailability {
  const [state, setState] = useState<CapabilityAvailability>({
    available: IS_MOCK_MODE,
    loading: !IS_MOCK_MODE
  });

  useEffect(() => {
    if (IS_MOCK_MODE) return;
    let active = true;
    void api.getCapabilities().then(catalog => {
      if (!active) return;
      const capability = catalog.data.find((item: CapabilityDescriptor) => (
        item.jobType === jobType && item.mode === "PLATFORM_API"
      ));
      setState({
        available: capability?.status === "LIVE",
        loading: false,
        reason: capability?.reason ?? "后端能力目录未登记该入口"
      });
    }).catch(reason => {
      if (!active) return;
      setState({
        available: false,
        loading: false,
        reason: reason instanceof Error ? reason.message : "能力目录读取失败"
      });
    });
    return () => {
      active = false;
    };
  }, [jobType]);

  return state;
}
