import { useState, useRef } from "react";
import {
  Plus,
  Trash2,
  CheckCircle,
  Upload,
  Download,
  Send,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toaster";

interface ScratchpadToolbarProps {
  selectedCount: number;
  readyCount: number;
  onAddRow: () => void;
  onDeleteSelected: () => void;
  onValidateSelected: () => void;
  onCommitReady: (postImmediately: boolean) => void;
  onImport: (file: File) => void;
  onExport: (format: "csv" | "xlsx") => void;
  onRefresh: () => void;
  isValidating?: boolean;
  isCommitting?: boolean;
  isImporting?: boolean;
  isExporting?: boolean;
}

export function ScratchpadToolbar({
  selectedCount,
  readyCount,
  onAddRow,
  onDeleteSelected,
  onValidateSelected,
  onCommitReady,
  onImport,
  onExport,
  onRefresh,
  isValidating = false,
  isCommitting = false,
  isImporting = false,
  isExporting = false,
}: ScratchpadToolbarProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [showCommitDialog, setShowCommitDialog] = useState(false);
  const [postImmediately, setPostImmediately] = useState(false);
  const { toast } = useToast();

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      // Validate file type
      const validTypes = [
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      ];
      if (!validTypes.includes(file.type) && !file.name.match(/\.(csv|xlsx?)$/i)) {
        toast({
          title: "Invalid file type",
          description: "Please upload a CSV or Excel file.",
          variant: "destructive",
        });
        return;
      }
      onImport(file);
    }
    // Reset the input
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleCommit = () => {
    setShowCommitDialog(false);
    onCommitReady(postImmediately);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 p-4 border-b bg-card/50">
      {/* Add row button */}
      <Button onClick={onAddRow} size="sm">
        <Plus className="me-2 h-4 w-4" />
        Add Row
      </Button>

      {/* Selected actions */}
      {selectedCount > 0 && (
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={onValidateSelected}
            disabled={isValidating}
          >
            <CheckCircle className="me-2 h-4 w-4" />
            {isValidating ? "Validating..." : `Validate (${selectedCount})`}
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={onDeleteSelected}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="me-2 h-4 w-4" />
            Delete ({selectedCount})
          </Button>
        </>
      )}

      {/* Commit button - shown when ready rows exist */}
      {readyCount > 0 && (
        <Button
          size="sm"
          variant="secondary"
          onClick={() => setShowCommitDialog(true)}
          disabled={isCommitting}
        >
          <Send className="me-2 h-4 w-4" />
          {isCommitting ? "Committing..." : `Commit Ready (${readyCount})`}
        </Button>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Import */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileSelect}
        accept=".csv,.xlsx,.xls"
        className="hidden"
      />
      <Button
        variant="outline"
        size="sm"
        onClick={() => fileInputRef.current?.click()}
        disabled={isImporting}
      >
        <Upload className="me-2 h-4 w-4" />
        {isImporting ? "Importing..." : "Import"}
      </Button>

      {/* Export */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={isExporting}>
            <Download className="me-2 h-4 w-4" />
            {isExporting ? "Exporting..." : "Export"}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => onExport("csv")}>
            Export as CSV
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => onExport("xlsx")}>
            Export as Excel
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Refresh */}
      <Button variant="ghost" size="sm" onClick={onRefresh}>
        <RefreshCw className="h-4 w-4" />
      </Button>

      {/* Commit confirmation dialog */}
      <Dialog open={showCommitDialog} onOpenChange={setShowCommitDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Commit Scratchpad Rows</DialogTitle>
            <DialogDescription>
              You have {readyCount} row(s) ready to commit. This will create
              journal entries for each group.
            </DialogDescription>
          </DialogHeader>

          <div className="py-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={postImmediately}
                onChange={(e) => setPostImmediately(e.target.checked)}
                className="rounded border-input"
              />
              <span className="text-sm">
                Post entries immediately (mark as POSTED)
              </span>
            </label>
            <p className="text-xs text-muted-foreground mt-2">
              If unchecked, entries will be created as DRAFT and can be reviewed
              before posting.
            </p>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCommitDialog(false)}>
              Cancel
            </Button>
            <Button onClick={handleCommit} disabled={isCommitting}>
              {isCommitting ? "Committing..." : "Commit"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
