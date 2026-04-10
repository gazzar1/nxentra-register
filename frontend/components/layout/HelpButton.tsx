import { useState } from "react";
import { HelpCircle, Send, MessageCircle, BookOpen, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toaster";
import { useAuth } from "@/contexts/AuthContext";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";

export function HelpButton() {
  const { user } = useAuth();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("question");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);

  const handleSubmit = async () => {
    if (!subject.trim() || !message.trim()) {
      toast({ title: "Please fill in all fields", variant: "destructive" });
      return;
    }
    setSending(true);
    try {
      // Send via mailto as a simple v1 — can be upgraded to API later
      const body = encodeURIComponent(
        `Category: ${category}\n` +
        `From: ${user?.name || ""} (${user?.email || ""})\n` +
        `Subject: ${subject}\n\n${message}`
      );
      const mailto = `mailto:support@nxentra.com?subject=${encodeURIComponent(`[${category}] ${subject}`)}&body=${body}`;
      window.open(mailto, "_blank");

      toast({ title: "Support request opened", description: "Your email client should open with the message. Send it to reach our team." });
      setSubject("");
      setMessage("");
      setOpen(false);
    } finally {
      setSending(false);
    }
  };

  const quickLinks = [
    { label: "Month-End Close Guide", href: "/settings/month-end-close", icon: BookOpen },
    { label: "System Health", href: "/settings/system-health", icon: HelpCircle },
  ];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DialogTrigger asChild>
              <Button variant="ghost" size="icon" className="relative">
                <HelpCircle className="h-5 w-5" />
              </Button>
            </DialogTrigger>
          </TooltipTrigger>
          <TooltipContent>Need help?</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <MessageCircle className="h-5 w-5" />
            Need Help?
          </DialogTitle>
          <DialogDescription>
            Ask a question, report an issue, or request a feature.
          </DialogDescription>
        </DialogHeader>

        {/* Quick Links */}
        <div className="flex gap-2 pb-2">
          {quickLinks.map((link) => (
            <a key={link.href} href={link.href} className="flex items-center gap-1.5 text-xs text-primary hover:underline px-2 py-1 rounded bg-muted">
              <link.icon className="h-3.5 w-3.5" />
              {link.label}
              <ExternalLink className="h-3 w-3" />
            </a>
          ))}
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Category</Label>
            <Select value={category} onValueChange={setCategory}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="question">Question</SelectItem>
                <SelectItem value="bug">Bug Report</SelectItem>
                <SelectItem value="feature">Feature Request</SelectItem>
                <SelectItem value="billing">Billing</SelectItem>
                <SelectItem value="urgent">Urgent Issue</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Subject</Label>
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Brief description of your issue"
            />
          </div>
          <div className="space-y-2">
            <Label>Message</Label>
            <Textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Describe what you need help with..."
              rows={4}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={sending}>
            <Send className="h-4 w-4 me-2" />
            {sending ? "Sending..." : "Send"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
