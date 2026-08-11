// Saving a file the browser already holds.
//
// Written once because the alternative is written twice, and the second copy is
// where the subtlety gets lost. The subtlety: **a plain `<a href>` to an API
// endpoint downloads a 401.** Every endpoint here needs an `Authorization`
// header, and a link element cannot carry one — so the file is fetched with the
// token like any other request and saved from the response body.
//
// The object URL is revoked after the click. It is a reference into the page's
// memory holding the whole file; a screen somebody exports from twice would
// otherwise hold both copies until the tab closes, and one of these is a full
// list of every row a sync could not resolve.

/** Save bytes the page already has under `filename`. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Save a value as pretty-printed JSON. */
export function saveJson(data: unknown, filename: string): void {
  saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
           filename);
}
