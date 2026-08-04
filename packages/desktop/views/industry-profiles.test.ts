import { describe, expect, it } from "vitest";
import {
  getIndustryProfile,
  INDUSTRY_PROFILES,
  PHASES,
  type IndustryKey,
} from "./industry-profiles";

const industryKeys = ["ops", "rd", "finance", "customer"] as const satisfies readonly IndustryKey[];

describe("industry profiles", () => {
  it.each(industryKeys)("defines a complete %s visualization profile", (key) => {
    const profile = INDUSTRY_PROFILES[key];

    expect(profile.pluginId).toBe(`industry-plugin-${key}`);
    expect(profile.workerRoles).toHaveLength(3);
    expect(new Set(profile.workerRoles.map((worker) => worker.id)).size).toBe(3);
    expect(profile.phases.map((item) => item.id)).toEqual(PHASES);
    expect(profile.phases).toHaveLength(6);
    expect(profile.metrics).toHaveLength(3);

    expect([
      profile.scheduler,
      profile.optimization,
      profile.security,
      profile.retrieval,
      profile.decision,
    ]).toHaveLength(5);
    expect(profile.scheduler.algorithms.length).toBeGreaterThan(0);
    expect(profile.optimization.algorithms.length).toBeGreaterThan(0);
    expect(profile.security.algorithms.length).toBeGreaterThan(0);
    expect(profile.retrieval.algorithms.length).toBeGreaterThan(0);
    expect(profile.decision.algorithms.length).toBeGreaterThan(0);
    expect(profile.fallback.pluginMissing.length).toBeGreaterThan(0);
    expect(profile.degradation.to).toBe("贪心优化");
  });

  it("returns the general fallback for an unknown or absent industry", () => {
    expect(getIndustryProfile("unknown")).toBe(INDUSTRY_PROFILES.general);
    expect(getIndustryProfile(undefined)).toBe(INDUSTRY_PROFILES.general);
    expect(getIndustryProfile(null)).toBe(INDUSTRY_PROFILES.general);
    expect(getIndustryProfile("ops")).toBe(INDUSTRY_PROFILES.ops);
  });
});
