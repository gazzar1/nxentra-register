import { useRouter } from "next/router";

interface BilingualTextProps {
  en: string;
  ar?: string;
  className?: string;
}

export function BilingualText({ en, ar, className }: BilingualTextProps) {
  const { locale } = useRouter();
  const text = locale === "ar" && ar ? ar : en;

  return <span className={className}>{text}</span>;
}

// Hook version for more flexibility
export function useBilingualText() {
  const { locale } = useRouter();

  return function getText(en: string, ar?: string): string {
    return locale === "ar" && ar ? ar : en;
  };
}
