import { AudioLines } from "lucide-react";
export default function Brand({ large = false }: { large?: boolean }) {
  return (
    <div className={`brand ${large ? "large" : ""}`}>
      <span className="brand-icon">
        <AudioLines size={large ? 30 : 22} />
      </span>
      <span>
        Canalla <b>LLM</b>
      </span>
    </div>
  );
}
