// A segmented control. Extracted from Tier2.tsx the moment a second screen
// needed one.
//
// The alternative was a second copy, and a segmented control is exactly the
// kind of thing where two copies drift: one grows `aria-pressed`, the other
// grows a focus ring, and a keyboard user gets a different experience on two
// screens that look identical. The CSS was already shared (`.seg-*` in
// viz.css); only the markup was duplicated, which is the worse half to
// duplicate.

export function Seg({
  label, value, onChange, options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  /** `[id, visible text]`. A tuple rather than an object because every call
   *  site writes these inline and the object form is three times the noise. */
  options: [string, string][];
}) {
  return (
    <span className="seg">
      <span className="seg-label">{label}</span>
      <span className="seg-buttons" role="group" aria-label={label}>
        {options.map(([id, text]) => (
          <button key={id} type="button"
                  className={value === id ? "seg-btn seg-on" : "seg-btn"}
                  aria-pressed={value === id}
                  onClick={() => onChange(id)}>
            {text}
          </button>
        ))}
      </span>
    </span>
  );
}
