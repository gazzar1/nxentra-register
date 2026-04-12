import Link from "next/link";
import Head from "next/head";

export default function PrivacyPolicyPage() {
  return (
    <>
      <Head>
        <title>Privacy Policy - Nxentra</title>
      </Head>
      <div className="min-h-screen bg-background text-foreground">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <nav className="mb-12">
            <Link href="/" className="text-sm text-accent hover:underline">
              &larr; Back to Nxentra
            </Link>
          </nav>

          <h1 className="text-3xl font-bold mb-2">Privacy Policy</h1>
          <p className="text-sm text-muted-foreground mb-8">
            Version 1.0 &mdash; Effective Date: April 12, 2026
          </p>

          <div className="prose prose-sm dark:prose-invert max-w-none space-y-6">
            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">1. Introduction</h2>
              <p>
                Nxentra (&quot;we&quot;, &quot;us&quot;, &quot;our&quot;) is committed to protecting your privacy. This Privacy
                Policy explains how we collect, use, store, and protect your personal information
                when you use our platform. By using Nxentra, you consent to the practices described
                in this policy.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">2. Information We Collect</h2>

              <h3 className="text-lg font-medium mt-4 mb-2">2.1 Information You Provide</h3>
              <ul className="list-disc pl-6 space-y-1">
                <li><strong>Account information:</strong> Email address, name, phone number, password, company name, preferred language</li>
                <li><strong>Financial data:</strong> Chart of accounts, journal entries, invoices, bills, bank transactions, inventory records, and other accounting data you enter</li>
                <li><strong>Integration credentials:</strong> OAuth tokens for Shopify, Stripe, and other connected platforms (stored encrypted)</li>
                <li><strong>Voice data:</strong> Audio recordings submitted through the voice entry feature (processed by OpenAI Whisper, not stored permanently)</li>
              </ul>

              <h3 className="text-lg font-medium mt-4 mb-2">2.2 Information Collected Automatically</h3>
              <ul className="list-disc pl-6 space-y-1">
                <li><strong>Usage data:</strong> Pages visited, features used, actions performed (for product improvement)</li>
                <li><strong>Device information:</strong> Browser type, operating system, IP address</li>
                <li><strong>Error data:</strong> Application errors and crash reports (via Sentry, if configured)</li>
              </ul>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">3. How We Use Your Information</h2>
              <p>We use your information to:</p>
              <ul className="list-disc pl-6 space-y-1">
                <li>Provide, maintain, and improve the Service</li>
                <li>Process your financial data and generate reports as requested</li>
                <li>Authenticate your identity and manage your account</li>
                <li>Send transactional emails (verification, password reset, notifications)</li>
                <li>Facilitate integrations with third-party platforms you connect</li>
                <li>Monitor and prevent security threats and abuse</li>
                <li>Comply with legal obligations</li>
              </ul>
              <p className="mt-3">
                <strong>We do not sell your personal information or financial data to third parties.</strong>
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">4. Data Storage and Security</h2>
              <ul className="list-disc pl-6 space-y-1">
                <li>Your data is stored in secure, access-controlled databases</li>
                <li>Financial data is isolated per company using PostgreSQL Row-Level Security (RLS) or dedicated databases</li>
                <li>Passwords are hashed using industry-standard algorithms (never stored in plain text)</li>
                <li>Authentication tokens are transmitted via encrypted HTTPS connections and stored in HttpOnly secure cookies</li>
                <li>All data in transit is encrypted using TLS 1.2 or higher</li>
                <li>We maintain an immutable audit trail of all financial transactions via our event-sourced architecture</li>
              </ul>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">5. Data Sharing</h2>
              <p>We may share your information only in the following circumstances:</p>
              <ul className="list-disc pl-6 space-y-1">
                <li><strong>With your consent:</strong> When you explicitly authorize a third-party integration (Shopify, Stripe, etc.)</li>
                <li><strong>Service providers:</strong> With trusted providers who assist in operating our Service (hosting, email delivery, error tracking), bound by confidentiality agreements</li>
                <li><strong>Legal requirements:</strong> When required by law, regulation, or legal process</li>
                <li><strong>Business transfers:</strong> In connection with a merger, acquisition, or sale of assets (with prior notice)</li>
              </ul>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">6. Data Retention</h2>
              <ul className="list-disc pl-6 space-y-1">
                <li>Your account and financial data are retained for as long as your account is active</li>
                <li>Upon account termination, you may request a data export within 30 days</li>
                <li>After the 30-day export window, your data will be permanently deleted within 90 days</li>
                <li>We may retain anonymized, aggregated data for analytics purposes</li>
                <li>Audit trail events may be retained longer where required by applicable financial regulations</li>
              </ul>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">7. Your Rights</h2>
              <p>Depending on your jurisdiction, you may have the right to:</p>
              <ul className="list-disc pl-6 space-y-1">
                <li><strong>Access:</strong> Request a copy of the personal data we hold about you</li>
                <li><strong>Correction:</strong> Request correction of inaccurate personal data</li>
                <li><strong>Deletion:</strong> Request deletion of your personal data (subject to legal retention requirements)</li>
                <li><strong>Export:</strong> Receive your data in a structured, machine-readable format</li>
                <li><strong>Restriction:</strong> Request restriction of processing in certain circumstances</li>
                <li><strong>Objection:</strong> Object to processing of your personal data for certain purposes</li>
              </ul>
              <p className="mt-3">
                To exercise any of these rights, contact us at{" "}
                <a href="mailto:admin@nxentra.com" className="text-accent underline">admin@nxentra.com</a>.
                We will respond within 30 days.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">8. Cookies</h2>
              <p>
                We use essential cookies required for the Service to function (authentication
                cookies, session management). We do not use advertising or third-party tracking
                cookies. Authentication cookies are HttpOnly and Secure, meaning they cannot be
                accessed by client-side scripts and are only transmitted over encrypted connections.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">9. International Data Transfers</h2>
              <p>
                Your data may be processed in countries other than your country of residence. When
                we transfer data internationally, we ensure appropriate safeguards are in place to
                protect your information in accordance with applicable data protection laws.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">10. Children&apos;s Privacy</h2>
              <p>
                The Service is not intended for use by individuals under the age of 18. We do not
                knowingly collect personal information from children. If you believe we have
                collected information from a child, please contact us immediately.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">11. Changes to This Policy</h2>
              <p>
                We may update this Privacy Policy from time to time. When we make material changes,
                we will notify you via email or through the Service and update the &quot;Effective Date&quot;
                above. Your continued use of the Service after changes constitutes acceptance of
                the updated policy.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold mt-8 mb-3">12. Contact</h2>
              <p>
                If you have questions or concerns about this Privacy Policy or our data practices,
                please contact us at:{" "}
                <a href="mailto:admin@nxentra.com" className="text-accent underline">
                  admin@nxentra.com
                </a>
              </p>
            </section>
          </div>
        </div>
      </div>
    </>
  );
}
