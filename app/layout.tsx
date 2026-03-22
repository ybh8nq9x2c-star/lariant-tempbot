import type { Metadata, Viewport } from "next";
import "./globals.css";

// ── PWA + Apple metadata ──────────────────────────────────────────────────────
export const metadata: Metadata = {
  title:       "LARIANT",
  description: "Hedge-fund grade weather prediction market terminal",
  manifest:    "/manifest.json",

  // Standard favicons + home-screen icons
  icons: {
    icon: [
      { url: "/favicon.ico",           sizes: "any" },
      { url: "/lariant-icon-192.png",  sizes: "192x192", type: "image/png" },
      { url: "/lariant-icon-512.png",  sizes: "512x512", type: "image/png" },
    ],
    // iOS home-screen: must be declared as `apple` to generate apple-touch-icon link tag
    apple: [
      { url: "/apple-touch-icon.png",  sizes: "180x180", type: "image/png" },
    ],
  },

  // iOS standalone PWA — enables full-screen mode when added to home screen
  appleWebApp: {
    capable:        true,
    title:          "LARIANT",
    // black-translucent: status bar overlays app content, dark bg extends
    // behind the Dynamic Island / notch on iPhone 15
    statusBarStyle: "black-translucent",
  },

  // Open Graph preview when shared via iMessage / social
  openGraph: {
    title:       "LARIANT",
    description: "Live weather prediction market terminal",
    type:        "website",
    images: [{ url: "/lariant-icon-512.png", width: 512, height: 512 }],
  },
};

// Viewport is a separate export in Next.js 14+ App Router.
// Keeping it separate avoids hydration warnings about viewport meta tags.
export const viewport: Viewport = {
  width:         "device-width",
  initialScale:  1,
  // Prevent auto-zoom on input focus (iOS default behaviour)
  maximumScale:  1,
  userScalable:  false,
  // Fills the Dynamic Island / notch with the black background
  viewportFit:   "cover",
  // Status bar tint — matches app background
  themeColor:    "#000000",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-black">{children}</body>
    </html>
  );
}
