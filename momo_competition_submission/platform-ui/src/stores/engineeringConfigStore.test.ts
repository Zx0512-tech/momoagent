import { describe, expect, it } from "vitest";
import type { DamperConnectionNodePair } from "../api/types";
import { createDamperRegistry } from "./engineeringConfigStore";

describe("custom damper connection nodes", () => {
  it("derives USER300 registry nodes from the user-defined node pairs", () => {
    const pairs: DamperConnectionNodePair[] = [
      {
        id: "north_custom_1",
        tower: "NORTH",
        nodeI: 36,
        nodeJ: 517,
        label: "北塔自定义节点 36-517"
      },
      {
        id: "south_custom_1",
        tower: "SOUTH",
        nodeI: 107,
        nodeJ: 520,
        label: "南塔自定义节点 107-520"
      }
    ];

    const registry = createDamperRegistry("VISCOUS", pairs, "LOCAL_AXIAL");

    expect(registry).toHaveLength(2);
    expect(registry.map(item => [item.nodeI, item.nodeJ])).toEqual([
      [36, 517],
      [107, 520]
    ]);
    expect(registry.every(item => item.source === "USER_DEFINED")).toBe(true);
  });

  it("rejects non-integer custom node identifiers", () => {
    const pairs: DamperConnectionNodePair[] = [
      {
        id: "invalid_pair",
        tower: "NORTH",
        nodeI: 36.5,
        nodeJ: 517,
        label: "无效节点对"
      }
    ];

    expect(createDamperRegistry("VISCOUS", pairs, "LOCAL_AXIAL")).toEqual([]);
  });
});
