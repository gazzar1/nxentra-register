import { useState, useRef, useCallback, useEffect } from "react";
import { Mic, MicOff, Loader2, AlertCircle, Check, Languages } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/cn";
import { scratchpadService, ParsedTransaction, VoiceParseResponse } from "@/services/scratchpad.service";

interface VoiceInputDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTransactionsCreated: (rowIds: string[]) => void;
}

type RecordingState = "idle" | "recording" | "processing" | "parsed" | "error";

export function VoiceInputDialog({
  open,
  onOpenChange,
  onTransactionsCreated,
}: VoiceInputDialogProps) {
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [language, setLanguage] = useState<"en" | "ar">("en");
  const [transcript, setTranscript] = useState("");
  const [parsedTransactions, setParsedTransactions] = useState<ParsedTransaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const recordingStartTimeRef = useRef<number | null>(null);

  // Cleanup on unmount or dialog close
  useEffect(() => {
    if (!open) {
      stopRecording();
      resetState();
    }
  }, [open]);

  const resetState = useCallback(() => {
    setRecordingState("idle");
    setTranscript("");
    setParsedTransactions([]);
    setError(null);
    audioChunksRef.current = [];
  }, []);

  const startRecording = useCallback(async () => {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "audio/mp4",
      });
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, {
          type: mediaRecorder.mimeType,
        });
        // Calculate recording duration in seconds
        const endTime = Date.now();
        const durationSeconds = recordingStartTimeRef.current
          ? (endTime - recordingStartTimeRef.current) / 1000
          : undefined;
        await processAudio(audioBlob, durationSeconds);
      };

      mediaRecorder.start(1000); // Collect data every second
      recordingStartTimeRef.current = Date.now();
      setRecordingState("recording");
    } catch (err) {
      console.error("Failed to start recording:", err);
      setError("Could not access microphone. Please check permissions.");
      setRecordingState("error");
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
      setRecordingState("processing");
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
  }, []);

  const processAudio = async (audioBlob: Blob, durationSeconds?: number) => {
    try {
      setRecordingState("processing");
      const response = await scratchpadService.parseVoiceAudio(audioBlob, {
        language,
        createRows: false, // Don't create rows yet, let user review first
        audioSeconds: durationSeconds,
      });

      if (response.data.success) {
        setTranscript(response.data.transcript);
        setParsedTransactions(response.data.transactions);
        setRecordingState("parsed");
      } else {
        setError(response.data.error || "Failed to parse audio");
        setRecordingState("error");
      }
    } catch (err: any) {
      console.error("Failed to process audio:", err);
      setError(err.response?.data?.error || "Failed to process recording");
      setRecordingState("error");
    }
  };

  const processTranscript = async () => {
    if (!transcript.trim()) return;

    try {
      setRecordingState("processing");
      const response = await scratchpadService.parseVoiceText(transcript, {
        language,
        createRows: false,
      });

      if (response.data.success) {
        setParsedTransactions(response.data.transactions);
        setRecordingState("parsed");
      } else {
        setError(response.data.error || "Failed to parse transcript");
        setRecordingState("error");
      }
    } catch (err: any) {
      console.error("Failed to parse transcript:", err);
      setError(err.response?.data?.error || "Failed to parse transcript");
      setRecordingState("error");
    }
  };

  const createRows = async () => {
    if (!transcript.trim() || parsedTransactions.length === 0) return;

    try {
      setIsCreating(true);
      // Use createFromParsed to avoid double API call (no re-parsing needed)
      const response = await scratchpadService.createFromParsed({
        transactions: parsedTransactions,
        transcript,
      });

      if (response.data.success && response.data.created_rows.length > 0) {
        onTransactionsCreated(response.data.created_rows);
        onOpenChange(false);
      } else {
        setError("Failed to create rows");
      }
    } catch (err: any) {
      console.error("Failed to create rows:", err);
      setError(err.response?.data?.error || "Failed to create rows");
    } finally {
      setIsCreating(false);
    }
  };

  const formatAmount = (amount: string | null) => {
    if (!amount) return "-";
    return new Intl.NumberFormat(language === "ar" ? "ar-SA" : "en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(parseFloat(amount));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Mic className="h-5 w-5" />
            Voice Input
          </DialogTitle>
          <DialogDescription>
            Record audio or type a description of your transaction(s).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Language selector */}
          <div className="flex items-center gap-4">
            <Label htmlFor="language" className="flex items-center gap-2">
              <Languages className="h-4 w-4" />
              Language
            </Label>
            <Select
              value={language}
              onValueChange={(val) => setLanguage(val as "en" | "ar")}
              disabled={recordingState === "recording" || recordingState === "processing"}
            >
              <SelectTrigger id="language" className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="en">English</SelectItem>
                <SelectItem value="ar">Arabic</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Recording controls */}
          <div className="flex flex-col items-center gap-4 p-6 border rounded-lg bg-muted/30">
            <button
              onClick={recordingState === "recording" ? stopRecording : startRecording}
              disabled={recordingState === "processing"}
              className={cn(
                "w-20 h-20 rounded-full flex items-center justify-center transition-all",
                recordingState === "recording"
                  ? "bg-red-500 hover:bg-red-600 animate-pulse"
                  : recordingState === "processing"
                  ? "bg-muted cursor-not-allowed"
                  : "bg-primary hover:bg-primary/90"
              )}
            >
              {recordingState === "processing" ? (
                <Loader2 className="h-8 w-8 text-muted-foreground animate-spin" />
              ) : recordingState === "recording" ? (
                <MicOff className="h-8 w-8 text-white" />
              ) : (
                <Mic className="h-8 w-8 text-primary-foreground" />
              )}
            </button>
            <p className="text-sm text-muted-foreground">
              {recordingState === "recording"
                ? "Recording... Click to stop"
                : recordingState === "processing"
                ? "Processing audio..."
                : "Click to start recording"}
            </p>
          </div>

          {/* Transcript input */}
          <div className="space-y-2">
            <Label htmlFor="transcript">
              Or type your transaction description:
            </Label>
            <Textarea
              id="transcript"
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder={
                language === "ar"
                  ? "مثال: سددت 5000 ريال للمورد أحمد من البنك الأهلي"
                  : "Example: Paid $5,000 to supplier Ahmed from bank account"
              }
              rows={3}
              dir={language === "ar" ? "rtl" : "ltr"}
              disabled={recordingState === "recording" || recordingState === "processing"}
            />
            {transcript && recordingState !== "parsed" && (
              <Button
                variant="outline"
                size="sm"
                onClick={processTranscript}
                disabled={recordingState === "processing"}
              >
                {recordingState === "processing" ? (
                  <>
                    <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    Parsing...
                  </>
                ) : (
                  "Parse Text"
                )}
              </Button>
            )}
          </div>

          {/* Error display */}
          {error && (
            <div className="flex items-center gap-2 p-3 rounded-lg bg-destructive/10 text-destructive">
              <AlertCircle className="h-4 w-4 flex-shrink-0" />
              <p className="text-sm">{error}</p>
            </div>
          )}

          {/* Parsed transactions preview */}
          {parsedTransactions.length > 0 && (
            <div className="space-y-2">
              <Label>Parsed Transactions:</Label>
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {parsedTransactions.map((tx, idx) => (
                  <div
                    key={idx}
                    className={cn(
                      "p-3 rounded-lg border bg-card",
                      tx.confidence < 0.7 && "border-yellow-500/50"
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="font-medium truncate">
                          {tx.description || tx.description_ar || "No description"}
                        </p>
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground mt-1">
                          {tx.transaction_date && (
                            <span>Date: {tx.transaction_date}</span>
                          )}
                          {tx.amount && (
                            <span className="font-mono">
                              Amount: {formatAmount(tx.amount)}
                            </span>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-2 mt-2 text-xs">
                          {tx.debit_account_code && (
                            <span className="px-2 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
                              Dr: {tx.debit_account_code}
                            </span>
                          )}
                          {tx.credit_account_code && (
                            <span className="px-2 py-0.5 rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300">
                              Cr: {tx.credit_account_code}
                            </span>
                          )}
                        </div>
                        {tx.suggestions.length > 0 && (
                          <div className="mt-2 text-xs text-yellow-600 dark:text-yellow-400">
                            {tx.suggestions.join("; ")}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-1 text-xs">
                        {tx.confidence >= 0.8 ? (
                          <Check className="h-4 w-4 text-green-500" />
                        ) : (
                          <AlertCircle className="h-4 w-4 text-yellow-500" />
                        )}
                        <span className="text-muted-foreground">
                          {Math.round(tx.confidence * 100)}%
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="outline" onClick={resetState} disabled={recordingState === "idle"}>
            Reset
          </Button>
          <Button
            onClick={createRows}
            disabled={
              parsedTransactions.length === 0 ||
              isCreating ||
              recordingState === "processing"
            }
          >
            {isCreating ? (
              <>
                <Loader2 className="me-2 h-4 w-4 animate-spin" />
                Creating...
              </>
            ) : (
              `Create ${parsedTransactions.length} Row(s)`
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
