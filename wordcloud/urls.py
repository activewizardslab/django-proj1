from django.conf.urls import patterns, include, url
from django.contrib import admin
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static

from filebrowser.sites import site


urlpatterns = patterns('',
                       url(r'^admin_tools/', include('admin_tools.urls')),
                       (r'^admin/filebrowser/', include(site.urls)),
                       (r'^grappelli/', include('grappelli.urls')),  # grappelli URLS
                       url(r'^admin/', include(admin.site.urls)),
                       url('^accounts/', include('django.contrib.auth.urls')),


                       url(r'^cloud/', include('cloud.urls')),
                       url(r'^wordle/', include('wordle.urls')),
                       url(r'^tree/', include('c_tree.urls')),
                       url(r'^pricing_tool', include('pricing_tool.urls')),

                       url(r'^$', TemplateView.as_view(template_name='wordcloud/base.html')),

                       ) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) + \
              static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG and settings.DEBUG_PANEL:
    import debug_toolbar

    urlpatterns += patterns('',
                            url(r'^__debug__/', include(debug_toolbar.urls)),
                            )
