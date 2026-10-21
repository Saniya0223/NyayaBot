'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BookOpenText, FileText, FolderOpen, MessageCircleMore, Scale } from 'lucide-react';
import { useAuth } from '@/components/AuthProvider';

const authenticatedLinks = [
  { href: '/', label: 'Ask NyayaBot', icon: MessageCircleMore },
  { href: '/cases', label: 'My Cases', icon: FolderOpen },
  { href: '/documents', label: 'Documents', icon: FileText },
  { href: '/statutes', label: 'Legal Resources', icon: BookOpenText },
];

const guestLinks = [
  { href: '/', label: 'Ask NyayaBot', icon: MessageCircleMore },
  { href: '/statutes', label: 'Legal Resources', icon: BookOpenText },
];

export default function Navbar() {
  const pathname = usePathname();
  const { user } = useAuth();

  return (
    <header className="sticky top-0 z-40 border-b border-[#eee8dc] bg-white/95 backdrop-blur-xl">
      <div className="mx-auto flex h-[70px] max-w-[1480px] items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex shrink-0 items-center gap-2.5 text-[#151515]" aria-label="NyayaBot home">
          <Scale className="size-8 sm:size-9" strokeWidth={1.8} aria-hidden="true" />
          <span className={`${user ? '' : 'hidden sm:inline'} text-[20px] font-extrabold leading-none tracking-[-0.05em] sm:text-[23px]`}>NyayaBot</span>
        </Link>
        <nav className="soft-scrollbar ml-2 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto sm:ml-5" aria-label="Primary navigation">
          {(user ? authenticatedLinks : guestLinks).map(({ href, label, icon: Icon }) => {
            const active = pathname === href || (href !== '/' && pathname.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                aria-label={label}
                aria-current={active ? 'page' : undefined}
                className={`flex shrink-0 items-center gap-2 rounded-xl px-2 py-2.5 text-[11px] font-semibold transition-colors sm:px-3 sm:text-sm ${active ? 'bg-gradient-to-r from-[#fff4c6] to-[#ffe891] text-[#151515]' : 'text-[#494740] hover:bg-[#fff8df] hover:text-[#151515]'}`}
              >
                <Icon className="size-4" aria-hidden="true" />
                <span className={user ? (href === '/' ? 'hidden sm:inline' : 'hidden lg:inline') : ''}>{label}</span>
              </Link>
            );
          })}
        </nav>
        {user ? (
          <Link href="/profile" aria-label="Profile" aria-current={pathname === '/profile' ? 'page' : undefined} className={`flex shrink-0 items-center gap-2 rounded-xl px-2 py-2.5 text-[11px] font-semibold transition-colors sm:px-3 sm:text-sm ${pathname === '/profile' ? 'bg-gradient-to-r from-[#fff4c6] to-[#ffe891] text-[#151515]' : 'text-[#494740] hover:bg-[#fff8df] hover:text-[#151515]'}`}>
            <span className="grid size-7 place-items-center rounded-full bg-[#fff0ad] text-xs font-bold text-[#151515]">{user.name.charAt(0).toUpperCase()}</span>
            <span className="hidden lg:inline">{user.name.split(' ')[0]}</span>
          </Link>
        ) : (
          <div className="flex shrink-0 items-center gap-1">
            <span className="mx-1 hidden h-6 w-px shrink-0 bg-[#dfd9cd] sm:block" aria-hidden="true" />
            <Link href="/login" aria-current={pathname === '/login' ? 'page' : undefined} className={`shrink-0 rounded-xl border px-2 py-2 text-[11px] font-bold transition sm:px-4 sm:text-sm ${pathname === '/login' ? 'border-[#e5c75c] bg-[#fff4c6] text-[#171717]' : 'border-[#e3ded4] bg-white text-[#171717] hover:bg-[#fff8df]'}`}>Login</Link>
            <Link href="/signup" aria-current={pathname === '/signup' ? 'page' : undefined} className="shrink-0 rounded-xl bg-[#171717] px-2 py-2 text-[11px] font-bold text-white transition hover:bg-[#303030] sm:px-4 sm:text-sm">Sign Up</Link>
          </div>
        )}
      </div>
    </header>
  );
}
