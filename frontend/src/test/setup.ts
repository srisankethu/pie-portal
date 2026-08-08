// Test setup, run once before every spec file.
//
// `@testing-library/jest-dom` adds the DOM matchers the component tests read
// with — `toBeInTheDocument`, `toHaveTextContent` — which say what they mean far
// better than poking at `container.innerHTML`.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount between tests. Without it a component from the previous test is still
// in `document.body`, and a `getByText` that should have failed finds the old
// one instead — the classic way a React suite goes green while the code is
// broken.
afterEach(cleanup);
