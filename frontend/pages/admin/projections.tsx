import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useState, useEffect, useCallback } from "react";
import {
  RefreshCw,
  Play,
  Pause,
  RotateCcw,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  Database,
  Activity,
  Clock,
  Zap,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Progress } from "@/components/ui/progress";
import { PageHeader, LoadingSpinner, EmptyState } from "@/components/common";
import { useAuth } from "@/contexts/AuthContext";
import {
  getProjections,
  rebuildProjection,
  pauseProjection,
  clearProjectionError,
  processProjection,
  type ProjectionInfo,
  type ProjectionListResponse,
} from "@/lib/api";
import { getAccessToken } from "@/lib/auth-storage";

export default function ProjectionsPage() {
  const { user } = useAuth();

  const [data, setData] = useState<ProjectionListResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Dialog state
  const [rebuildDialogOpen, setRebuildDialogOpen] = useState(false);
  const [selectedProjection, setSelectedProjection] =
    useState<ProjectionInfo | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Expanded rows for details
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const fetchProjections = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const token = getAccessToken();
      if (!token) {
        setError("Not authenticated");
        return;
      }
      const result = await getProjections(token);
      setData(result);
    } catch (err) {
      console.error(err);
      setError("Failed to load projections");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjections();
  }, [fetchProjections]);

  // Clear success message after 5 seconds
  useEffect(() => {
    if (successMessage) {
      const timer = setTimeout(() => setSuccessMessage(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [successMessage]);

  // Auto-refresh when any projection is rebuilding
  useEffect(() => {
    if (data?.any_rebuilding) {
      const interval = setInterval(fetchProjections, 3000);
      return () => clearInterval(interval);
    }
  }, [data?.any_rebuilding, fetchProjections]);

  const toggleExpand = (name: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const openRebuildDialog = (projection: ProjectionInfo) => {
    setSelectedProjection(projection);
    setRebuildDialogOpen(true);
  };

  const handleRebuild = async (force: boolean = false) => {
    if (!selectedProjection) return;

    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;

      const result = await rebuildProjection(
        token,
        selectedProjection.name,
        force
      );
      setSuccessMessage(
        `${selectedProjection.name}: ${result.events_processed} events processed in ${result.duration_seconds}s`
      );
      setRebuildDialogOpen(false);
      fetchProjections();
    } catch (err: unknown) {
      console.error(err);
      const errorMessage =
        err instanceof Error ? err.message : "Failed to rebuild projection";
      setError(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handlePause = async (projection: ProjectionInfo) => {
    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;

      const newPaused = !projection.is_paused;
      await pauseProjection(token, projection.name, newPaused);
      setSuccessMessage(
        `${projection.name} ${newPaused ? "paused" : "resumed"}`
      );
      fetchProjections();
    } catch (err) {
      console.error(err);
      setError("Failed to update projection");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClearError = async (projection: ProjectionInfo) => {
    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;

      await clearProjectionError(token, projection.name);
      setSuccessMessage(`Errors cleared for ${projection.name}`);
      fetchProjections();
    } catch (err) {
      console.error(err);
      setError("Failed to clear errors");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleProcess = async (projection: ProjectionInfo) => {
    try {
      setIsSubmitting(true);
      setError(null);
      const token = getAccessToken();
      if (!token) return;

      const result = await processProjection(token, projection.name);
      setSuccessMessage(
        `${projection.name}: ${result.events_processed} events processed, ${result.remaining_lag} remaining`
      );
      fetchProjections();
    } catch (err) {
      console.error(err);
      setError("Failed to process events");
    } finally {
      setIsSubmitting(false);
    }
  };

  const formatDate = (dateString: string | null) => {
    if (!dateString) return "Never";
    return new Date(dateString).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return "—";
    if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  };

  const getStatusBadge = (projection: ProjectionInfo) => {
    if (projection.is_rebuilding) {
      return (
        <Badge variant="default" className="bg-blue-500">
          <Loader2 className="me-1 h-3 w-3 animate-spin" />
          Rebuilding {projection.rebuild_progress_percent.toFixed(0)}%
        </Badge>
      );
    }

    if (projection.rebuild_status === "ERROR" || projection.error_count > 0) {
      return (
        <Badge variant="destructive">
          <XCircle className="me-1 h-3 w-3" />
          Error
        </Badge>
      );
    }

    if (projection.is_paused) {
      return (
        <Badge variant="secondary">
          <Pause className="me-1 h-3 w-3" />
          Paused
        </Badge>
      );
    }

    if (!projection.is_healthy) {
      return (
        <Badge variant="outline" className="border-yellow-500 text-yellow-500">
          <AlertTriangle className="me-1 h-3 w-3" />
          Lag: {projection.lag}
        </Badge>
      );
    }

    return (
      <Badge variant="outline" className="border-green-500 text-green-500">
        <CheckCircle className="me-1 h-3 w-3" />
        Healthy
      </Badge>
    );
  };

  // Check if user is admin
  if (user && !user.is_staff && !user.is_superuser) {
    return (
      <AppLayout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <Card className="max-w-md">
            <CardHeader>
              <CardTitle>Access Denied</CardTitle>
              <CardDescription>
                You do not have permission to access this page. Only
                administrators can manage projections.
              </CardDescription>
            </CardHeader>
          </Card>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Projection Management"
          subtitle="Monitor and manage event projections for your company"
          actions={
            <Button
              variant="outline"
              onClick={fetchProjections}
              disabled={isLoading}
            >
              <RefreshCw
                className={`me-2 h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
              />
              Refresh
            </Button>
          }
        />

        {error && (
          <Card className="border-destructive">
            <CardContent className="pt-6">
              <p className="text-destructive">{error}</p>
            </CardContent>
          </Card>
        )}

        {successMessage && (
          <Card className="border-green-500 bg-green-500/10">
            <CardContent className="pt-6">
              <p className="text-green-400">{successMessage}</p>
            </CardContent>
          </Card>
        )}

        {/* Summary Cards */}
        {data && (
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">
                  Projections
                </CardTitle>
                <Database className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {data.projections.length}
                </div>
                <p className="text-xs text-muted-foreground">
                  registered projections
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Health</CardTitle>
                <Activity className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {data.all_healthy ? (
                    <span className="text-green-500">All Healthy</span>
                  ) : (
                    <span className="text-yellow-500">
                      {data.projections.filter((p) => !p.is_healthy).length}{" "}
                      Behind
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  {data.total_lag} total events behind
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Status</CardTitle>
                <Zap className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {data.any_rebuilding ? (
                    <span className="text-blue-500">Rebuilding</span>
                  ) : (
                    <span className="text-green-500">Ready</span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  {data.projections.filter((p) => p.is_paused).length} paused
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Company</CardTitle>
                <Clock className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold truncate">
                  {data.company.name}
                </div>
                <p className="text-xs text-muted-foreground">
                  {data.company.slug}
                </p>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Projections Table */}
        <Card>
          <CardHeader>
            <CardTitle>Projections</CardTitle>
            <CardDescription>
              Event projections build materialized views from your event stream.
              Rebuild when data gets out of sync.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="flex justify-center py-12">
                <LoadingSpinner size="lg" />
              </div>
            ) : !data || data.projections.length === 0 ? (
              <EmptyState
                title="No projections found"
                description="There are no registered projections for this company."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8"></TableHead>
                    <TableHead>Projection</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Lag</TableHead>
                    <TableHead>Last Rebuild</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.projections.map((projection) => (
                    <Collapsible
                      key={projection.name}
                      open={expandedRows.has(projection.name)}
                      onOpenChange={() => toggleExpand(projection.name)}
                      asChild
                    >
                      <>
                        <TableRow className="group">
                          <TableCell>
                            <CollapsibleTrigger asChild>
                              <Button variant="ghost" size="sm" className="p-0">
                                {expandedRows.has(projection.name) ? (
                                  <ChevronDown className="h-4 w-4" />
                                ) : (
                                  <ChevronRight className="h-4 w-4" />
                                )}
                              </Button>
                            </CollapsibleTrigger>
                          </TableCell>
                          <TableCell>
                            <div>
                              <p className="font-medium">{projection.name}</p>
                              <p className="text-xs text-muted-foreground">
                                {projection.consumes.join(", ") || "all events"}
                              </p>
                            </div>
                          </TableCell>
                          <TableCell>{getStatusBadge(projection)}</TableCell>
                          <TableCell>
                            {projection.lag > 0 ? (
                              <span className="text-yellow-500 font-medium">
                                {projection.lag.toLocaleString()}
                              </span>
                            ) : (
                              <span className="text-muted-foreground">0</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <div className="text-sm">
                              {formatDate(
                                projection.last_rebuild_completed_at
                              )}
                              {projection.last_rebuild_duration_seconds && (
                                <span className="text-xs text-muted-foreground block">
                                  {formatDuration(
                                    projection.last_rebuild_duration_seconds
                                  )}
                                </span>
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex justify-end gap-2">
                              {projection.lag > 0 && !projection.is_rebuilding && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => handleProcess(projection)}
                                  disabled={isSubmitting || projection.is_paused}
                                  title="Process pending events"
                                >
                                  <Play className="h-4 w-4" />
                                </Button>
                              )}
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => handlePause(projection)}
                                disabled={isSubmitting || projection.is_rebuilding}
                                title={
                                  projection.is_paused
                                    ? "Resume projection"
                                    : "Pause projection"
                                }
                              >
                                {projection.is_paused ? (
                                  <Play className="h-4 w-4" />
                                ) : (
                                  <Pause className="h-4 w-4" />
                                )}
                              </Button>
                              <Button
                                size="sm"
                                variant="default"
                                onClick={() => openRebuildDialog(projection)}
                                disabled={isSubmitting || projection.is_rebuilding}
                              >
                                <RotateCcw className="me-1 h-4 w-4" />
                                Rebuild
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>

                        <CollapsibleContent asChild>
                          <TableRow className="bg-muted/30">
                            <TableCell colSpan={6}>
                              <div className="py-4 px-2 space-y-4">
                                {/* Progress bar if rebuilding */}
                                {projection.is_rebuilding && (
                                  <div className="space-y-2">
                                    <div className="flex justify-between text-sm">
                                      <span>Rebuild Progress</span>
                                      <span>
                                        {projection.events_processed.toLocaleString()}{" "}
                                        / {projection.events_total.toLocaleString()}{" "}
                                        events
                                      </span>
                                    </div>
                                    <Progress
                                      value={projection.rebuild_progress_percent}
                                    />
                                  </div>
                                )}

                                {/* Error message */}
                                {projection.error_message && (
                                  <div className="bg-destructive/10 border border-destructive/50 rounded p-3">
                                    <div className="flex items-start justify-between">
                                      <div>
                                        <p className="text-sm font-medium text-destructive">
                                          Error ({projection.error_count} total)
                                        </p>
                                        <p className="text-xs text-destructive/80 mt-1">
                                          {projection.error_message}
                                        </p>
                                      </div>
                                      <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() =>
                                          handleClearError(projection)
                                        }
                                        disabled={isSubmitting}
                                      >
                                        Clear Error
                                      </Button>
                                    </div>
                                  </div>
                                )}

                                {/* Details grid */}
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                  <div>
                                    <p className="text-muted-foreground">
                                      Event Types
                                    </p>
                                    <p className="font-medium">
                                      {projection.consumes.length > 0
                                        ? projection.consumes.join(", ")
                                        : "All events"}
                                    </p>
                                  </div>
                                  <div>
                                    <p className="text-muted-foreground">
                                      Last Processed
                                    </p>
                                    <p className="font-medium">
                                      {formatDate(projection.last_processed_at)}
                                    </p>
                                  </div>
                                  <div>
                                    <p className="text-muted-foreground">
                                      Bookmark Errors
                                    </p>
                                    <p className="font-medium">
                                      {projection.bookmark_error_count}
                                    </p>
                                  </div>
                                  <div>
                                    <p className="text-muted-foreground">
                                      Last Rebuild Started
                                    </p>
                                    <p className="font-medium">
                                      {formatDate(
                                        projection.last_rebuild_started_at
                                      )}
                                    </p>
                                  </div>
                                </div>

                                {/* Bookmark error message */}
                                {projection.bookmark_last_error && (
                                  <div className="text-xs text-destructive/80 bg-destructive/5 p-2 rounded">
                                    Bookmark error: {projection.bookmark_last_error}
                                  </div>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        </CollapsibleContent>
                      </>
                    </Collapsible>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Rebuild Confirmation Dialog */}
      <Dialog open={rebuildDialogOpen} onOpenChange={setRebuildDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rebuild Projection</DialogTitle>
            <DialogDescription>
              This will clear all projected data for{" "}
              <strong>{selectedProjection?.name}</strong> and rebuild it from
              the event stream. This operation may take some time for large
              datasets.
            </DialogDescription>
          </DialogHeader>

          {selectedProjection?.is_rebuilding && (
            <div className="py-4">
              <div className="bg-yellow-500/10 border border-yellow-500/50 rounded p-3">
                <p className="text-sm text-yellow-500">
                  A rebuild is already in progress (
                  {selectedProjection.rebuild_progress_percent.toFixed(0)}%
                  complete). You can force a restart if needed.
                </p>
              </div>
            </div>
          )}

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRebuildDialogOpen(false)}
            >
              Cancel
            </Button>
            {selectedProjection?.is_rebuilding ? (
              <Button
                variant="destructive"
                onClick={() => handleRebuild(true)}
                disabled={isSubmitting}
              >
                {isSubmitting ? "Rebuilding..." : "Force Restart"}
              </Button>
            ) : (
              <Button onClick={() => handleRebuild(false)} disabled={isSubmitting}>
                {isSubmitting ? (
                  <>
                    <Loader2 className="me-2 h-4 w-4 animate-spin" />
                    Rebuilding...
                  </>
                ) : (
                  "Start Rebuild"
                )}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}

export const getServerSideProps: GetServerSideProps = async ({ locale }) => {
  return {
    props: {
      ...(await serverSideTranslations(locale ?? "en", ["common", "settings"])),
    },
  };
};
