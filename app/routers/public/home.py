from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.core.config import get_settings
from app.models.catalog import Movie, MovieStatus, Series

router = APIRouter()

# The help page is static prose: the questions and answers live here so the
# template stays a pure presentation layer and no migration or DB read is needed.
FAQ = [
    (
        "What is this site?",
        "This is a community-run movie and TV series hub. Members upload titles with their\nIMDb link and the official trailer; we import the details from IMDb automatically.\nEvery upload is reviewed by our team before it appears.",
    ),
    (
        "How do I upload a movie or series?",
        "Sign in, open the Contribute page, and paste the IMDb link together with 2 or more\ndownloads links. The title, year, poster and description are imported for you.\nAdmins and users with auto-approve permission see it live straight away;\neveryone else has it reviewed by a staff member.",
    ),
    (
        "What are the Secure Vault links?",
        "The vault is the list of watch and download sources a title has been given.\nEach link is submitted by a member, approved by a human, and only then shown on the page.\nClicking the links opens the source in a new tab — we are not the hosts.",
    ),
    (
        "How do points work?",
        "You earn points for approved uploads and for invitations that turn into a real\naccount. Your available balance is your lifetime total minus points held in a pending\ninvitation. The leaderboard ranks by available points, so spending does not cost you rank\npermanently — it only reflects what you can still spend.",
    ),
    (
        "What are the reputation levels?",
        "Levels are purely cosmetic ranks earned from your point history: Newcomer, Contributor,\nVeteran, Expert, Legend and Hall of Fame. They show on your profile and the leaderboard\nbut grant no extra permissions.",
    ),
    (
        "How do invitations work?",
        "If you have enough available points you can invite someone. The invite link is emailed\ndirectly to the address you enter and expires after 7 days. Points are held while the\ninvitation is pending and released if it expires unused.",
    ),
    (
        "Why is my upload not showing?",
        "Uploads wait in a review queue unless you have auto-approve permission. Track the\nstatus on My contributions — it flips from pending to approved once a staff member\nreviews it, and the points land at that moment.",
    ),
    (
        "How do I reset my password?",
        "On the login page choose Forgot password and enter your email. We send a reset link\nthat is valid for a short time. If you never receive it, check the spam folder first.",
    ),
    (
        "How do I verify my email?",
        "After registering you receive a verification link. Open it once and your account is\nconfirmed. You can resend the link from your profile if it expires.",
    ),
    (
        "Is this site legal?",
        "We host no video files. Every title is identified by its IMDb page and every watch\nlink points to a third party. Linking to a source is not the same as hosting it.\nMembers who knowingly post infringing material can have their links removed and their\naccount suspended.",
    ),
    (
        "Can I edit my display name?",
        "Yes — open your profile and save a new display name (2 to 100 characters). It is\nshown on your contributions and on the leaderboard.",
    ),
]


@router.get("/help/", response_class=HTMLResponse, include_in_schema=False)
async def help_center(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "public/help.html",
        {"page_title": "Help", "faq": FAQ, "support_email": settings.support_email},
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    featured_movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED, Movie.is_featured == True)
            .order_by(Movie.published_at.desc())
            .limit(5)
        )
    ).all()
    featured_series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED, Series.is_featured == True)
            .order_by(Series.published_at.desc())
            .limit(5)
        )
    ).all()
    featured = featured_movies + featured_series
    exclusive_movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED, Movie.is_exclusive == True)
            .order_by(Movie.published_at.desc())
            .limit(10)
        )
    ).all()
    exclusive_series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED, Series.is_exclusive == True)
            .order_by(Series.published_at.desc())
            .limit(10)
        )
    ).all()
    latest_movies = (
        await session.scalars(
            select(Movie).where(Movie.status == MovieStatus.PUBLISHED).order_by(Movie.published_at.desc()).limit(12)
        )
    ).all()
    latest_series = (
        await session.scalars(
            select(Series).where(Series.status == MovieStatus.PUBLISHED).order_by(Series.published_at.desc()).limit(12)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "public/home.html",
        {
            "page_title": "Home",
            "featured": featured,
            "exclusives": exclusive_movies + exclusive_series,
            "latest_movies": latest_movies,
            "latest_series": latest_series,
        },
    )
