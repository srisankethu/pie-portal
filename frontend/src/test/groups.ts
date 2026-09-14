// One group, as the server sends it, for the two suites that need one.
//
// Shared rather than imported from a sibling `.test.tsx`. Importing one test
// file from another runs its `describe`s a second time under the importing
// file's name, so the same four assertions were reported twice and a failure
// pointed at a file that did not contain it. A fixture belongs beside the other
// shared harness in this directory.
import type { EntityGroup } from "../platform/types";

export function aGroup(over: Partial<EntityGroup> = {}): EntityGroup {
  return {
    slug: "aerospace", name: "Aerospace", description: null,
    entity_kind: "CUSTOMER", entity_label: "Customers", membership: "ROSTER",
    visibility: "OPERATIONAL", group_version: "gr_abc1234567", members: 12,
    created_by: "M. Rao", archived: false, updated_at: null,
    ...over,
  };
}
