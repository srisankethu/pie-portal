// The shared controls the visualization screens put in their header strip.
//
// A segmented control, and the window picker that sits beside it. Extracted
// from Patterns.tsx the moment a second screen needed one.
//
// The alternative was a second copy, and a segmented control is exactly the
// kind of thing where two copies drift: one grows `aria-pressed`, the other
// grows a focus ring, and a keyboard user gets a different experience on two
// screens that look identical. The CSS was already shared (`.seg-*` in
// viz.css); only the markup was duplicated, which is the worse half to
// duplicate.
//
// Now a `ToggleButtonGroup`, so the same thing holds against the rest of the
// app rather than just against its own second copy: one focus treatment, one
// hit target, one selected style, shared with every other control on screen.
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";
import Box from "@mui/material/Box";

/** The label that precedes a header control. Shared so "BY", "MEASURE" and
 *  "MONTHS" are one typographic decision rather than three. */
function ControlLabel({ children, htmlFor }: { children: string; htmlFor?: string }) {
  return (
    <Typography
      component="label"
      htmlFor={htmlFor}
      variant="overline"
      sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
    >
      {children}
    </Typography>
  );
}

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
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
      <ControlLabel>{label}</ControlLabel>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={value}
        aria-label={label}
        // `exclusive` reports null when the pressed button is clicked again.
        // Passing that through would leave the screen with no measure selected
        // and every chart empty, so a second click on the current option is a
        // no-op rather than a deselect.
        onChange={(_, next) => { if (next !== null) onChange(next as string); }}
      >
        {options.map(([id, text]) => (
          <ToggleButton key={id} value={id} sx={{ py: 0.4 }}>
            {text}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
    </Box>
  );
}

/** How many months the screen looks back over.
 *
 * There were four of these — in Screens, Patterns, History and Storyboard — each a
 * hand-written `<select className="input">`, each with its own label wording and
 * its own idea of whether the option said "12" or "12 months". Four copies of
 * one control is the responsibility duplication CLAUDE.md §2 names: nothing was
 * exactly the same, so nothing looked like a duplicate, and the screens
 * disagreed on what the window control was called.
 *
 * The variation that is real — which windows a screen offers, and what the
 * control is called there — is parameterised. The rest is settled here.
 */
export function MonthPicker({
  id, value, onChange, options, label = "Months", long = false,
}: {
  id: string;
  value: number;
  onChange: (n: number) => void;
  options: number[];
  /** "Months" everywhere except the storyboard, which is comparing periods
   *  rather than plotting them and says "Compare". */
  label?: string;
  /** Spell out the unit in each option. Worth it when the picker stands alone;
   *  noise when it sits under a label that already says "Months". */
  long?: boolean;
}) {
  return (
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
      <ControlLabel htmlFor={id}>{label}</ControlLabel>
      <TextField
        id={id}
        select
        size="small"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        sx={{ minWidth: long ? 118 : 76 }}
      >
        {options.map((m) => (
          <MenuItem key={m} value={m}>
            {long ? `${m} month${m === 1 ? "" : "s"}` : m}
          </MenuItem>
        ))}
      </TextField>
    </Box>
  );
}
