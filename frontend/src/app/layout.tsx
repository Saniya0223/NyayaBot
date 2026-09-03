import type { Metadata } from 'next';
import './globals.css';
import Navbar from '@/components/Navbar';

export const metadata: Metadata = {
  title: 'NyayaBot | Turn your story into a legal action plan',
  description: 'A conversation-first legal rights and action assistant for Indian citizens.',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col antialiased">
        <Navbar />
        <main className="w-full flex-1">{children}</main>
        <footer className="border-t border-[#dfe6e2] bg-white px-5 py-4 text-center text-[11px] leading-relaxed text-[#6c7873]">
          <p className="mx-auto max-w-3xl">
            NyayaBot provides legal information and drafting assistance, not legal representation. Laws, forums, and deadlines can depend on your facts and State; verify important steps with an advocate or legal-services authority.
          </p>
        </footer>
      </body>
    </html>
  );
}
