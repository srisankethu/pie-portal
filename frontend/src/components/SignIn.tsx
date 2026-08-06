import { api } from "../api";
import type { Session } from "../types";
import { SignInCard } from "../SignInCard";

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  return (
    <SignInCard
      title="Quote Builder"
      blurb="Sign in to review RFQs, pick supply options, and create estimates with confidence."
      submitLabel="Continue to quote builder"
      onSubmit={async (email, password) => onSignedIn(await api.login(email, password))}
      // The footer used to list two accounts and say "Any password works".
      // That was true of the demo login this replaced and has been false since
      // real authentication landed — a sentence on the sign-in screen telling
      // people the password does not matter is worth removing on its own.
      footer={
        "Your account decides your role. An owner creates accounts and sets roles " +
        "from Settings; if you have not been given one, ask them."
      }
    />
  );
}
