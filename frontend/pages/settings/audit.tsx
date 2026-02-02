import { useState } from "react";
import { GetServerSideProps } from "next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import { useTranslation } from "next-i18next";
import { formatDistanceToNow, format } from "date-fns";
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  Database,
  Activity,
  RefreshCw,
  ChevronRight,
  User,
  Bot,
  Webhook,
  Server,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Clock,
  FileText,
  Link2,
} from "lucide-react";
import { AppLayout } from "@/components/layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, LoadingSpinner } from "@/components/common";
import { useToast } from "@/components/ui/toaster";
import { getErrorMessage } from "@/lib/api-client";
import {
  useEvents,
  useEvent,
  useEventChain,
  useIntegritySummary,
  useIntegrityCheck,
  useEventBookmarks,
} from "@/queries/useEvents";
import type {
  BusinessEvent,
  BusinessEventDetail,
  EventListParams,
} from "@/services/events.service";

// Origin icon mapping
const originIcons: Record<string, React.ReactNode> = {
  human: <User className="h-4 w-4" />,
  batch: <FileText className="h-4 w-4" />,
  api: <Webhook className="h-4 w-4" />,
  system: <Server className="h-4 w-4" />,
};

const originLabels: Record<string, string> = {
  human: "Manual",
  batch: "Batch Import",
  api: "API",
  system: "System",
};

// Storage badge colors
const storageBadgeVariants: Record<string, "default" | "secondary" | "outline"> = {
  inline: "default",
  external: "secondary",
  chunked: "outline",
};

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

// Integrity Summary Card
function IntegritySummaryCard() {
  const { t } = useTranslation(["common", "settings"]);
  const { data: summary, isLoading, refetch } = useIntegritySummary();
  const integrityCheck = useIntegrityCheck();
  const { toast } = useToast();

  const handleRunCheck = async () => {
    try {
      const result = await integrityCheck.mutateAsync();
      if (result.is_valid) {
        toast({
          title: "Integrity Check Passed",
          description: `Verified ${result.verified_events} events successfully.`,
          variant: "success",
        });
      } else {
        toast({
          title: "Integrity Issues Found",
          description: `Found ${result.payload_errors.length} errors and ${result.sequence_gaps.length} gaps.`,
          variant: "destructive",
        });
      }
    } catch (error) {
      toast({
        title: "Error",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-48" />
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            {[1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-20" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const isHealthy = summary && !summary.has_potential_gaps;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div className="flex items-center gap-3">
          {isHealthy ? (
            <ShieldCheck className="h-6 w-6 text-green-500" />
          ) : (
            <ShieldAlert className="h-6 w-6 text-amber-500" />
          )}
          <div>
            <CardTitle>Event Stream Integrity</CardTitle>
            <CardDescription>
              {isHealthy
                ? "All events verified, no gaps detected"
                : "Potential issues detected - run full check"}
            </CardDescription>
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetch()}
            disabled={isLoading}
          >
            <RefreshCw className="h-4 w-4 me-2" />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={handleRunCheck}
            disabled={integrityCheck.isPending}
          >
            {integrityCheck.isPending ? (
              <LoadingSpinner size="sm" className="me-2" />
            ) : (
              <Shield className="h-4 w-4 me-2" />
            )}
            Run Full Check
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 md:grid-cols-4">
          <div className="rounded-lg border p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Database className="h-4 w-4" />
              <span className="text-sm font-medium">Total Events</span>
            </div>
            <p className="text-2xl font-bold">{summary?.total_events.toLocaleString()}</p>
          </div>

          <div className="rounded-lg border p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Activity className="h-4 w-4" />
              <span className="text-sm font-medium">Max Sequence</span>
            </div>
            <p className="text-2xl font-bold">{summary?.max_sequence.toLocaleString()}</p>
          </div>

          <div className="rounded-lg border p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Link2 className="h-4 w-4" />
              <span className="text-sm font-medium">External Payloads</span>
            </div>
            <p className="text-2xl font-bold">{summary?.external_payload_count.toLocaleString()}</p>
          </div>

          <div className="rounded-lg border p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <FileText className="h-4 w-4" />
              <span className="text-sm font-medium">Chunked Events</span>
            </div>
            <p className="text-2xl font-bold">{summary?.chunked_event_count.toLocaleString()}</p>
          </div>
        </div>

        {/* Storage & Origin Breakdown */}
        {summary && (
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <div>
              <h4 className="text-sm font-medium text-muted-foreground mb-2">
                Storage Distribution
              </h4>
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.storage_breakdown).map(([type, count]) => (
                  <Badge key={type} variant={storageBadgeVariants[type] || "default"}>
                    {type}: {count.toLocaleString()}
                  </Badge>
                ))}
              </div>
            </div>
            <div>
              <h4 className="text-sm font-medium text-muted-foreground mb-2">
                Origin Distribution
              </h4>
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.origin_breakdown).map(([origin, count]) => (
                  <Badge key={origin} variant="outline" className="gap-1">
                    {originIcons[origin]}
                    {originLabels[origin] || origin}: {count.toLocaleString()}
                  </Badge>
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// Event Detail Dialog
function EventDetailDialog({
  event,
  open,
  onOpenChange,
}: {
  event: BusinessEvent | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: detail, isLoading } = useEvent(event?.id || "");
  const { data: chain } = useEventChain(event?.id || "");

  if (!event) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Badge variant="outline">{event.event_type}</Badge>
            Event Details
          </DialogTitle>
          <DialogDescription>
            ID: {event.id}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-20" />
            <Skeleton className="h-40" />
          </div>
        ) : detail ? (
          <div className="space-y-6">
            {/* Basic Info */}
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <Label className="text-muted-foreground">Aggregate</Label>
                <p className="font-mono">
                  {detail.aggregate_type}/{detail.aggregate_id}
                </p>
              </div>
              <div>
                <Label className="text-muted-foreground">Sequence</Label>
                <p>
                  #{detail.sequence} (Company: #{detail.company_sequence})
                </p>
              </div>
              <div>
                <Label className="text-muted-foreground">Origin</Label>
                <p className="flex items-center gap-2">
                  {originIcons[detail.origin]}
                  {originLabels[detail.origin]}
                </p>
              </div>
              <div>
                <Label className="text-muted-foreground">Storage</Label>
                <p className="flex items-center gap-2">
                  <Badge variant={storageBadgeVariants[detail.payload_storage]}>
                    {detail.payload_storage}
                  </Badge>
                  {detail.payload_hash && (
                    <span className="font-mono text-xs text-muted-foreground">
                      {detail.payload_hash.substring(0, 16)}...
                    </span>
                  )}
                </p>
              </div>
              <div>
                <Label className="text-muted-foreground">Occurred At</Label>
                <p>{format(new Date(detail.occurred_at), "PPpp")}</p>
              </div>
              <div>
                <Label className="text-muted-foreground">Caused By</Label>
                <p>
                  {detail.caused_by_user_email || (
                    <span className="text-muted-foreground">System</span>
                  )}
                </p>
              </div>
            </div>

            {/* Causation Chain */}
            {chain && (chain.parent || chain.children.length > 0) && (
              <div>
                <Label className="text-muted-foreground">Causation Chain</Label>
                <div className="mt-2 rounded-lg border p-3 space-y-2">
                  {chain.parent && (
                    <div className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Parent:</span>
                      <Badge variant="outline">{chain.parent.event_type}</Badge>
                      <span className="font-mono text-xs">
                        {chain.parent.id.substring(0, 8)}...
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-2 text-sm">
                    <ChevronRight className="h-4 w-4" />
                    <Badge>{event.event_type}</Badge>
                    <span className="text-muted-foreground">
                      (depth: {chain.chain_depth})
                    </span>
                  </div>
                  {chain.children.length > 0 && (
                    <div className="ps-6 space-y-1">
                      {chain.children.map((child) => (
                        <div
                          key={child.id}
                          className="flex items-center gap-2 text-sm"
                        >
                          <ChevronRight className="h-4 w-4 text-muted-foreground" />
                          <Badge variant="outline">{child.event_type}</Badge>
                          <span className="font-mono text-xs">
                            {child.id.substring(0, 8)}...
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Payload Data */}
            <div>
              <Label className="text-muted-foreground">Resolved Payload</Label>
              <pre className="mt-2 rounded-lg bg-muted p-4 text-xs overflow-x-auto max-h-60">
                {JSON.stringify(detail.resolved_data, null, 2)}
              </pre>
            </div>

            {/* External Payload Info */}
            {detail.payload_ref_info && (
              <div>
                <Label className="text-muted-foreground">
                  External Payload Reference
                </Label>
                <div className="mt-2 rounded-lg border p-3 text-sm">
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <span className="text-muted-foreground">Size:</span>{" "}
                      {formatBytes(detail.payload_ref_info.size_bytes)}
                    </div>
                    <div>
                      <span className="text-muted-foreground">Compression:</span>{" "}
                      {detail.payload_ref_info.compression || "none"}
                    </div>
                    <div className="col-span-2">
                      <span className="text-muted-foreground">Hash:</span>{" "}
                      <span className="font-mono text-xs">
                        {detail.payload_ref_info.content_hash}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

// Events Table
function EventsTable() {
  const { t } = useTranslation(["common", "settings"]);
  const [filters, setFilters] = useState<EventListParams>({});
  const [selectedEvent, setSelectedEvent] = useState<BusinessEvent | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const { data: events, isLoading, refetch } = useEvents(filters);

  const handleEventClick = (event: BusinessEvent) => {
    setSelectedEvent(event);
    setDetailOpen(true);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Event Log</CardTitle>
            <CardDescription>
              Browse and inspect individual events
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 me-2" />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {/* Filters */}
        <div className="flex flex-wrap gap-4 mb-4">
          <div className="flex-1 min-w-[200px]">
            <Label className="text-xs text-muted-foreground">Event Type</Label>
            <Input
              placeholder="e.g., journal_entry.posted"
              value={filters.event_type || ""}
              onChange={(e) =>
                setFilters({ ...filters, event_type: e.target.value || undefined })
              }
            />
          </div>
          <div className="w-[150px]">
            <Label className="text-xs text-muted-foreground">Origin</Label>
            <Select
              value={filters.origin || "all"}
              onValueChange={(value) =>
                setFilters({
                  ...filters,
                  origin: value === "all" ? undefined : value,
                })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Origins</SelectItem>
                <SelectItem value="human">Manual</SelectItem>
                <SelectItem value="batch">Batch Import</SelectItem>
                <SelectItem value="api">API</SelectItem>
                <SelectItem value="system">System</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex-1 min-w-[200px]">
            <Label className="text-xs text-muted-foreground">Aggregate ID</Label>
            <Input
              placeholder="UUID or identifier"
              value={filters.aggregate_id || ""}
              onChange={(e) =>
                setFilters({
                  ...filters,
                  aggregate_id: e.target.value || undefined,
                })
              }
            />
          </div>
        </div>

        {/* Table */}
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Event Type</TableHead>
                  <TableHead>Aggregate</TableHead>
                  <TableHead>Origin</TableHead>
                  <TableHead>Storage</TableHead>
                  <TableHead>Occurred</TableHead>
                  <TableHead className="w-[50px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events && events.length > 0 ? (
                  events.map((event) => (
                    <TableRow
                      key={event.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => handleEventClick(event)}
                    >
                      <TableCell>
                        <Badge variant="outline" className="font-mono text-xs">
                          {event.event_type}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {event.aggregate_type}/{event.aggregate_id.substring(0, 8)}...
                      </TableCell>
                      <TableCell>
                        <span className="flex items-center gap-1">
                          {originIcons[event.origin]}
                          <span className="text-xs">
                            {originLabels[event.origin]}
                          </span>
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={storageBadgeVariants[event.payload_storage]}>
                          {event.payload_storage}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDistanceToNow(new Date(event.occurred_at), {
                          addSuffix: true,
                        })}
                      </TableCell>
                      <TableCell>
                        <ChevronRight className="h-4 w-4 text-muted-foreground" />
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className="text-center text-muted-foreground py-8"
                    >
                      No events found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Event Detail Dialog */}
        <EventDetailDialog
          event={selectedEvent}
          open={detailOpen}
          onOpenChange={setDetailOpen}
        />
      </CardContent>
    </Card>
  );
}

// Projection Bookmarks Table
function BookmarksTable() {
  const { data: bookmarks, isLoading, refetch } = useEventBookmarks();

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Projection Consumers</CardTitle>
            <CardDescription>
              Track projection processing status and lag
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 me-2" />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Consumer</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last Processed</TableHead>
                  <TableHead>Errors</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {bookmarks && bookmarks.length > 0 ? (
                  bookmarks.map((bookmark) => (
                    <TableRow key={bookmark.id}>
                      <TableCell className="font-medium">
                        {bookmark.consumer_name}
                      </TableCell>
                      <TableCell>
                        {bookmark.is_paused ? (
                          <Badge variant="secondary" className="gap-1">
                            <Clock className="h-3 w-3" />
                            Paused
                          </Badge>
                        ) : bookmark.error_count > 0 ? (
                          <Badge variant="destructive" className="gap-1">
                            <AlertTriangle className="h-3 w-3" />
                            Error
                          </Badge>
                        ) : (
                          <Badge variant="default" className="gap-1 bg-green-500">
                            <CheckCircle2 className="h-3 w-3" />
                            Running
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {bookmark.last_processed_at
                          ? formatDistanceToNow(
                              new Date(bookmark.last_processed_at),
                              { addSuffix: true }
                            )
                          : "Never"}
                      </TableCell>
                      <TableCell>
                        {bookmark.error_count > 0 ? (
                          <span className="text-destructive font-medium">
                            {bookmark.error_count}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">0</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="text-center text-muted-foreground py-8"
                    >
                      No projection consumers found
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// Main Page Component
export default function AuditPage() {
  const { t } = useTranslation(["common", "settings"]);

  return (
    <AppLayout>
      <div className="space-y-6">
        <PageHeader
          title="Event Audit"
          subtitle="Monitor event stream integrity and browse audit trail"
        />

        <Tabs defaultValue="overview" className="space-y-6">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="events">Event Log</TabsTrigger>
            <TabsTrigger value="projections">Projections</TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="space-y-6">
            <IntegritySummaryCard />
          </TabsContent>

          <TabsContent value="events">
            <EventsTable />
          </TabsContent>

          <TabsContent value="projections">
            <BookmarksTable />
          </TabsContent>
        </Tabs>
      </div>
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
