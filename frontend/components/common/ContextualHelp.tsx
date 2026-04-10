import { useState } from "react";
import { HelpCircle, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

interface HelpItem {
  question: string;
  answer: string;
}

interface ContextualHelpProps {
  items: HelpItem[];
  className?: string;
}

export function ContextualHelp({ items, className }: ContextualHelpProps) {
  const [open, setOpen] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (items.length === 0) return null;

  return (
    <div className={cn("rounded-lg border border-muted bg-muted/30", className)}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-2.5 flex items-center justify-between text-start"
      >
        <div className="flex items-center gap-2">
          <HelpCircle className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">Common questions</span>
        </div>
        <ChevronDown className={cn("h-4 w-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="px-4 pb-3 space-y-1">
          {items.map((item, idx) => (
            <div key={idx} className="border-t border-muted pt-1">
              <button
                onClick={() => setExpandedIdx(expandedIdx === idx ? null : idx)}
                className="w-full flex items-start justify-between py-2 text-start"
              >
                <span className="text-sm font-medium pe-4">{item.question}</span>
                <ChevronDown className={cn("h-3.5 w-3.5 text-muted-foreground shrink-0 mt-0.5 transition-transform", expandedIdx === idx && "rotate-180")} />
              </button>
              {expandedIdx === idx && (
                <p className="text-sm text-muted-foreground pb-2 ps-0">{item.answer}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
