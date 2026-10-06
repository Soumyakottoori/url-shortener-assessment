using System.Reflection;
using Microsoft.AspNetCore.Authorization;
using Microsoft.OpenApi.Models;
using Swashbuckle.AspNetCore.SwaggerGen;

namespace UrlShortener.Api.Swagger;

public sealed class AuthorizeOperationFilter : IOperationFilter
{
    public void Apply(OpenApiOperation operation, OperationFilterContext context)
    {
        var method = context.MethodInfo;
        var controller = method.DeclaringType;
        var allowsAnonymous = method.GetCustomAttributes(true).OfType<AllowAnonymousAttribute>().Any() ||
            controller?.GetCustomAttributes(true).OfType<AllowAnonymousAttribute>().Any() == true;
        var requiresAuthorization = method.GetCustomAttributes(true).OfType<AuthorizeAttribute>().Any() ||
            controller?.GetCustomAttributes(true).OfType<AuthorizeAttribute>().Any() == true;

        if (!requiresAuthorization || allowsAnonymous)
            return;

        operation.Security.Add(new OpenApiSecurityRequirement
        {
            [new OpenApiSecurityScheme
            {
                Reference = new OpenApiReference
                {
                    Type = ReferenceType.SecurityScheme,
                    Id = "Bearer"
                }
            }] = Array.Empty<string>()
        });
    }
}