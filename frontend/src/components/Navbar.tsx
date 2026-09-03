'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BookOpenText, FileText, FolderOpen, MessageCircleMore, Scale, UserRound } from 'lucide-react';

const links = [
  { href: '/', label: 'Ask NyayaBot', icon: MessageCircleMore },
  { href: '/cases', label: 'My Cases', icon: FolderOpen },
  { href: '/documents', label: 'Documents', icon: FileText },
  { href: '/statutes', label: 'Legal Resources', icon: BookOpenText },
  { href: '/profile', label: 'Profile', icon: UserRound },
];

export default function Navbar() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b border-[#dfe6e2] bg-white/95 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1480px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex shrink-0 items-center gap-2.5" aria-label="NyayaBot home">
          <span className="grid size-9 place-items-center rounded-xl bg-[#174e3b] text-white shadow-sm">
            <Scale className="size-[18px]" aria-hidden="true" />
          </span>
          <span>
            <span className="block text-[17px] font-bold leading-none tracking-[-0.02em] text-[#17231f]">NyayaBot</span>
            <span className="mt-1 hidden text-[10px] font-medium text-[#718078] sm:block">Legal action, made clearer</span>
          </span>
        </Link>
        <nav className="flex min-w-0 items-center gap-1 overflow-x-auto soft-scrollbar" aria-label="Primary navigation">
          {links.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href !== '/' && pathname.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                aria-label={label}
                aria-current={active ? 'page' : undefined}
                className={`flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold transition-colors sm:text-sm ${active ? 'bg-[#e8f2ec] text-[#174e3b]' : 'text-[#69766f] hover:bg-[#f3f6f4] hover:text-[#25342e]'}`}
              >
                <Icon className="size-4" aria-hidden="true" />
                <span className={href === '/' ? 'hidden sm:inline' : 'hidden lg:inline'}>{label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
