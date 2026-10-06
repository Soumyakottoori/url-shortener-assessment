using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using UrlShortener.Api.Services;

namespace UrlShortener.Api.Controllers;

[ApiController]
[Route("api/links")]
[Authorize(Policy = "AdminOnly")]
[EnableRateLimiting("admin")]
public sealed class LinksController(ILinkService linkService) : ControllerBase
{
    [HttpPost]
    [ProducesResponseType<CreateLinkResponse>(StatusCodes.Status201Created)]
    public async Task<ActionResult<CreateLinkResponse>> Create(CreateLinkRequest request, CancellationToken cancellationToken)
    {
        var code = await linkService.CreateAsync(request.Url, request.Alias, request.TtlSeconds, cancellationToken);
        var publicBaseUrl = Environment.GetEnvironmentVariable("PUBLIC_BASE_URL")?.TrimEnd('/')
            ?? $"{Request.Scheme}://{Request.Host}";
        return StatusCode(StatusCodes.Status201Created, new CreateLinkResponse(code, $"{publicBaseUrl}/r/{code}"));
    }

    [HttpGet("{code}/stats")]
    public Task<LinkStats> GetStats(string code, CancellationToken cancellationToken) =>
        linkService.GetStatsAsync(code, cancellationToken);

    [HttpDelete("{code}")]
    public async Task<IActionResult> Disable(string code, CancellationToken cancellationToken)
    {
        await linkService.DisableAsync(code, cancellationToken);
        return NoContent();
    }
}