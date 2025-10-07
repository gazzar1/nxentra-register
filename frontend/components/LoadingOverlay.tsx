import { motion } from "framer-motion";

interface LoadingOverlayProps {
  message?: string;
}

export function LoadingOverlay({ message = "Setting up your workspace..." }: LoadingOverlayProps) {
  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-6 rounded-3xl bg-slate-950/90">
      <motion.div
        className="h-24 w-24 rounded-full border-4 border-slate-800 border-t-accent"
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, ease: "linear", duration: 1 }}
      />
      <motion.p
        className="text-lg font-medium text-slate-200"
        animate={{ opacity: [0.3, 1, 0.3] }}
        transition={{ repeat: Infinity, duration: 2 }}
      >
        {message}
      </motion.p>
    </div>
  );
}
