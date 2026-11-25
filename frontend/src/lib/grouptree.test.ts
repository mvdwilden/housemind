import { describe, expect, it } from "vitest";
import { groupTree } from "./grouptree";
import type { DeviceRow } from "./types";

function row(id: string, group?: string[], radio = true): DeviceRow {
  return {
    id, name: id, area: "Badkamer", device: id, deviceId: id, typeKey: "light", part: "lighting", step: "autonomous",
    domain: "Licht", kind: "light.dimmable", safety: null, maxStep: null, controls: true, excluded: false, state: "off",
    ...(group ? { group: { source: radio ? "zigbee2mqtt" : "ha", sourceName: "", radio, members: group, steers: false } } : {}),
  };
}

describe("groups with their devices in Apparaten en entiteiten (E-312)", () => {
  it("puts a smaller group inside the bigger one and each lamp under the smallest group", () => {
    const lamps = ["wash1", "wash2", "shower1", "shower2", "wc"].map((id) => row(id));
    const bath = row("bath", ["wash1", "wash2", "shower1", "shower2", "wc"]);
    const wash = row("wash", ["wash1", "wash2"]);
    const shower = row("shower", ["shower1", "shower2"]);
    const other = row("mirror");
    const list = [...lamps, bath, wash, shower, other];
    const tree = groupTree(list, list);
    expect(tree.top.map((g) => g.id)).toEqual(["bath"]);
    expect(tree.children.get("bath")?.map((g) => g.id).sort()).toEqual(["shower", "wash"]);
    expect(tree.members.get("wash")?.map((d) => d.id)).toEqual(["wash1", "wash2"]);
    expect(tree.members.get("bath")?.map((d) => d.id)).toEqual(["wc"]); // the others sit in the smaller groups
    expect(tree.loose.map((d) => d.id)).toEqual(["mirror"]);
  });

  it("names the devices of a group that live in other rooms", () => {
    const here = [row("garden_group", ["spot1", "spot2"]), row("spot1")];
    const all = [...here, { ...row("spot2"), name: "Oprit spot" }];
    const tree = groupTree(here, all);
    expect(tree.members.get("garden_group")?.map((d) => d.id)).toEqual(["spot1"]);
    expect(tree.elsewhere.get("garden_group")).toEqual(["Oprit spot"]);
  });

  it("an HA group of the same lamps sits inside the radio group, not twice on top", () => {
    const list = [row("a"), row("b"), row("z2m", ["a", "b"]), row("ha", ["a", "b"], false)];
    const tree = groupTree(list, list);
    expect(tree.top.map((g) => g.id)).toEqual(["z2m"]);
    expect(tree.children.get("z2m")?.map((g) => g.id)).toEqual(["ha"]);
  });
});
